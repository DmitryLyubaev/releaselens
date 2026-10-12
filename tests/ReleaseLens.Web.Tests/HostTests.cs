using System.Net;
using System.Text.Json;
using ReleaseLens.Web;

namespace ReleaseLens.Web.Tests;

public sealed class HostTests
{
    [Theory]
    [InlineData(CallerKind.Owner, "owner")]
    [InlineData(CallerKind.Deployment, "deployment")]
    public async Task HealthHasNoDownstreamCallsOrPersonalInformation(CallerKind caller, string label)
    {
        await using var factory = new WebAppFactory();
        using var client = factory.As(caller);
        using var response = await client.GetAsync("/health", TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var text = await response.Content.ReadAsStringAsync(TestContext.Current.CancellationToken);
        using var json = JsonDocument.Parse(text);
        Assert.Equal(label, json.RootElement.GetProperty("caller").GetString());
        Assert.Equal("main", json.RootElement.GetProperty("slot").GetString());
        Assert.Equal(5, json.RootElement.EnumerateObject().Count());
        Assert.DoesNotContain(Fixtures.Owner, text);
        Assert.DoesNotContain(Fixtures.Deploy, text);
        Assert.DoesNotContain("email", text);
        Assert.Empty(factory.Stub.Requests);
    }

    [Theory]
    [InlineData("/")]
    [InlineData("/ask")]
    [InlineData("/session")]
    public async Task DeploymentCannotUseOwnerRoutes(string route)
    {
        await using var factory = new WebAppFactory();
        using var client = factory.As(CallerKind.Deployment);
        using var response = route == "/ask" ? await client.PostAsync(route, new StringContent(""), TestContext.Current.CancellationToken) : await client.GetAsync(route, TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.Forbidden, response.StatusCode);
        Assert.Empty(factory.Stub.Requests);
    }

    [Fact]
    public async Task AnonymousRequestsAreRedirectedToFixedSignIn()
    {
        await using var factory = new WebAppFactory();
        using var client = factory.CreateClient(new Microsoft.AspNetCore.Mvc.Testing.WebApplicationFactoryClientOptions { AllowAutoRedirect = false });
        using var response = await client.GetAsync("/", TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.Redirect, response.StatusCode);
        Assert.Equal("/.auth/login/aad?post_login_redirect_uri=/", response.Headers.Location?.OriginalString);
    }

    [Fact]
    public async Task SignedOutPageIsPublicAndDoesNotExposeIdentity()
    {
        await using var factory = new WebAppFactory();
        using var response = await factory.CreateClient().GetAsync("/signed-out", TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.DoesNotContain(Fixtures.Owner, await response.Content.ReadAsStringAsync(TestContext.Current.CancellationToken));
    }

    [Fact]
    public async Task SessionIsNoStoreAndUnavailableUntilInspectionExists()
    {
        await using var factory = new WebAppFactory();
        using var response = await factory.As(CallerKind.Owner).GetAsync("/session", TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.ServiceUnavailable, response.StatusCode);
        Assert.True(response.Headers.CacheControl?.NoStore);
        Assert.Empty(factory.Stub.Requests);
    }

    [Theory]
    [InlineData("Web:GatewayBaseUrl", "http://unsafe.example.test/")]
    [InlineData("Web:OwnerObjectId", "secret-marker")]
    [InlineData("Web:Model", "")]
    [InlineData("Web:Slot", "arbitrary")]
    public async Task InvalidConfigurationGivesGenericUnhealthyResponse(string key, string value)
    {
        await using var factory = new WebAppFactory();
        factory.Config[key] = value;
        using var response = await factory.As(CallerKind.Owner).GetAsync("/health", TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.ServiceUnavailable, response.StatusCode);
        var text = await response.Content.ReadAsStringAsync(TestContext.Current.CancellationToken);
        Assert.Contains("\"configurationValid\":false", text);
        Assert.DoesNotContain("secret-marker", text);
        Assert.DoesNotContain("example.test", text);
    }
}
