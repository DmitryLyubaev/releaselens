using System;
using Azure.Core;
using Azure.Identity;
using ReleaseLens.Llm.Providers.Azure;
using Xunit;

namespace ReleaseLens.Llm.Tests;

public class AzureCredentialFactoryTests
{
    private const string ClientId = "00000000-0000-0000-0000-000000000001";
    private const string TenantId = "00000000-0000-0000-0000-000000000002";

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("   ")]
    public void Create_ManagedIdentityWithoutAClientId_Throws(string? clientId)
    {
        var failure = Assert.Throws<InvalidOperationException>(() => AzureCredentialFactory.Create(
            new AzureOpenAiOptions { Credential = AzureCredentialKind.ManagedIdentity, ClientId = clientId }));

        Assert.Contains("AZURE_CLIENT_ID", failure.Message, StringComparison.Ordinal);
    }

    [Fact]
    public void Create_ManagedIdentityWithAClientId_IsAManagedIdentityCredential()
    {
        var credential = AzureCredentialFactory.Create(new AzureOpenAiOptions
        {
            Credential = AzureCredentialKind.ManagedIdentity,
            ClientId = ClientId
        });

        Assert.IsType<ManagedIdentityCredential>(credential);
    }

    // Spec 4.7: capped at one retry with a short delay, because Azure.Core's default is three
    // retries with exponential backoff, a wait the per-query 429 budget knows nothing about.
    [Fact]
    public void ManagedIdentityOptions_CapTheRetryAtOneWithAShortFixedDelay()
    {
        var retry = AzureCredentialFactory.ManagedIdentityOptions(new AzureOpenAiOptions
        {
            Credential = AzureCredentialKind.ManagedIdentity,
            ClientId = ClientId
        }).Retry;

        Assert.Equal(1, retry.MaxRetries);
        Assert.Equal(RetryMode.Fixed, retry.Mode);
        Assert.Equal(TimeSpan.FromMilliseconds(200), retry.Delay);
        Assert.Equal(TimeSpan.FromMilliseconds(200), retry.MaxDelay);
    }

    [Fact]
    public void Create_AzureCli_IsAnAzureCliCredential()
    {
        var credential = AzureCredentialFactory.Create(new AzureOpenAiOptions
        {
            Credential = AzureCredentialKind.AzureCli,
            TenantId = TenantId
        });

        Assert.IsType<AzureCliCredential>(credential);
    }

    // Spec 4.7: the Azure CLI credential with the tenant pinned. Without one, az answers for
    // whichever tenant it is signed in to.
    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("   ")]
    public void Create_AzureCliWithoutATenantId_Throws(string? tenantId)
    {
        var failure = Assert.Throws<InvalidOperationException>(() => AzureCredentialFactory.Create(
            new AzureOpenAiOptions { Credential = AzureCredentialKind.AzureCli, TenantId = tenantId }));

        Assert.Contains("AzureOpenAi:TenantId", failure.Message, StringComparison.Ordinal);
    }
}
