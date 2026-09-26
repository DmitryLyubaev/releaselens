using System;
using System.Collections.Generic;
using System.Threading.Tasks;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.Hosting;
using ReleaseLens.Storage.Tests;
using Xunit;

namespace ReleaseLens.Api.Tests;

/// <summary>
/// Configurations the app must refuse to start with, and the ones next to them that it must
/// accept. Each refused one would otherwise start cleanly and fail, or answer from the wrong
/// chain, only when the first query arrives.
/// </summary>
/// <remarks>
/// Nothing here touches the database, but the class still joins the Postgres collection:
/// Program reads RELEASELENS_DB and the provider keys from the process environment, which
/// every class in that collection sets, and AZURE_CLIENT_ID is cleared here, so running
/// beside them would race.
/// </remarks>
[Collection(nameof(PostgresCollection))]
public class StartupConfigurationTests(PostgresFixture fixture) : IAsyncLifetime
{
    private string? _clientIdBefore;

    public ValueTask InitializeAsync()
    {
        Environment.SetEnvironmentVariable("RELEASELENS_DB", fixture.ConnectionString);
        Environment.SetEnvironmentVariable("ANTHROPIC_API_KEY", "sk-ant-test");
        Environment.SetEnvironmentVariable("OPENAI_API_KEY", "sk-test");

        // A developer machine may have AZURE_CLIENT_ID set for something else; the managed
        // identity cases below need it absent.
        _clientIdBefore = Environment.GetEnvironmentVariable("AZURE_CLIENT_ID");
        Environment.SetEnvironmentVariable("AZURE_CLIENT_ID", null);

        return ValueTask.CompletedTask;
    }

    public ValueTask DisposeAsync()
    {
        Environment.SetEnvironmentVariable("AZURE_CLIENT_ID", _clientIdBefore);
        return ValueTask.CompletedTask;
    }

    // UseSetting is already visible to the options Program reads before Build. appsettings.json
    // has no Chat:Providers, so a case's list is exactly the indexes it sets, and a case that
    // sets none runs ChatOptions' default (anthropic, openai). The cases that name two providers
    // set index 0 and index 1 because each index is its own key. The Azure cases put anthropic
    // after azure-openai, so each check is made with Azure beside another priced provider.
    private static WebApplicationFactory<Program> Factory(IReadOnlyDictionary<string, string> settings) =>
        new WebApplicationFactory<Program>().WithWebHostBuilder(builder =>
        {
            builder.UseEnvironment("Testing");

            foreach (var (key, value) in settings)
            {
                builder.UseSetting(key, value);
            }
        });

    // Reading Services starts the host, which runs Program to its end, startup checks included.
    private static void Start(WebApplicationFactory<Program> factory) => _ = factory.Services;

    private static Dictionary<string, string> AzureConfigured(string first, string second) => new()
    {
        ["Chat:Providers:0"] = first,
        ["Chat:Providers:1"] = second,
        ["AzureOpenAi:BaseUrl"] = "http://127.0.0.1:9/openai/v1/",
        ["AzureOpenAi:Deployment"] = "gpt-4.1-mini-test",

        // The Azure CLI credential (the default) is refused without a pinned tenant.
        ["AzureOpenAi:TenantId"] = "00000000-0000-0000-0000-000000000002"
    };

    [Fact]
    public async Task Chat_UnknownProviderName_FailsStartup_NamingTheValidProviders()
    {
        await using var factory = Factory(new Dictionary<string, string>
        {
            ["Chat:Providers:0"] = "anthropc",
            ["Chat:Providers:1"] = "openai"
        });

        var exception = Assert.Throws<InvalidOperationException>(() => Start(factory));

        Assert.Contains("'anthropc'", exception.Message, StringComparison.Ordinal);

        // The list comes from the keys Program registers, so this also pins that all three
        // providers are wired.
        Assert.Contains("Valid providers: anthropic, azure-openai, openai.", exception.Message, StringComparison.Ordinal);
    }

    [Fact]
    public async Task OpenAi_ModelWithNoRate_FailsStartup()
    {
        await using var factory = Factory(new Dictionary<string, string>
        {
            ["OpenAi:Model"] = "gpt-no-rate-test"
        });

        var exception = Assert.Throws<InvalidOperationException>(() => Start(factory));

        Assert.Contains("gpt-no-rate-test", exception.Message, StringComparison.Ordinal);
    }

    [Fact]
    public async Task OpenAi_ModelWithNoRate_MarkedUnpriced_Starts()
    {
        await using var factory = Factory(new Dictionary<string, string>
        {
            ["OpenAi:Model"] = "gpt-no-rate-test",
            ["OpenAi:Unpriced"] = "true"
        });

        Start(factory);
    }

    [Fact]
    public async Task Azure_NotListed_NeverBuildsACredential()
    {
        // With no client ID, building this credential throws. The app starting proves that a
        // chain without azure-openai never builds it.
        await using var factory = Factory(new Dictionary<string, string>
        {
            ["AzureOpenAi:Credential"] = "ManagedIdentity"
        });

        Start(factory);
    }

    [Fact]
    public async Task Azure_Listed_WithManagedIdentityAndNoClientId_FailsStartup()
    {
        // The same settings with the Azure CLI credential start (the last test), so the
        // credential is what fails here.
        var settings = AzureConfigured("azure-openai", "anthropic");
        settings["AzureOpenAi:Credential"] = "ManagedIdentity";

        await using var factory = Factory(settings);

        var exception = Assert.Throws<InvalidOperationException>(() => Start(factory));

        Assert.Contains("AZURE_CLIENT_ID", exception.Message, StringComparison.Ordinal);
    }

    [Fact]
    public async Task Azure_Listed_WithTheAzureCliCredentialAndNoTenantId_FailsStartup_NamingTheSetting()
    {
        // The same settings with a tenant start (the last test), so the missing tenant is what
        // fails here.
        var settings = AzureConfigured("azure-openai", "anthropic");
        settings.Remove("AzureOpenAi:TenantId");

        await using var factory = Factory(settings);

        var exception = Assert.Throws<InvalidOperationException>(() => Start(factory));

        Assert.Contains("AzureOpenAi:TenantId", exception.Message, StringComparison.Ordinal);
    }

    [Fact]
    public async Task Azure_Listed_WithoutBaseUrlOrDeployment_FailsStartup_NamingBoth()
    {
        await using var factory = Factory(new Dictionary<string, string>
        {
            ["Chat:Providers:0"] = "azure-openai",
            ["Chat:Providers:1"] = "anthropic"
        });

        var exception = Assert.Throws<InvalidOperationException>(() => Start(factory));

        Assert.Contains("AzureOpenAi:BaseUrl", exception.Message, StringComparison.Ordinal);
        Assert.Contains("AzureOpenAi:Deployment", exception.Message, StringComparison.Ordinal);
    }

    [Fact]
    public async Task Azure_Listed_WithADeploymentTypeThatHasNoRate_FailsStartup()
    {
        var settings = AzureConfigured("azure-openai", "anthropic");
        settings["AzureOpenAi:DeploymentType"] = "GlobalStandard";

        await using var factory = Factory(settings);

        var exception = Assert.Throws<InvalidOperationException>(() => Start(factory));

        Assert.Contains("GlobalStandard", exception.Message, StringComparison.Ordinal);
    }

    [Fact]
    public async Task Azure_Listed_WithTheAzureCliCredential_Starts()
    {
        // AzureCliCredential runs az only when asked for a token, and nothing asks at startup.
        await using var factory = Factory(AzureConfigured("azure-openai", "anthropic"));

        Start(factory);
    }
}
