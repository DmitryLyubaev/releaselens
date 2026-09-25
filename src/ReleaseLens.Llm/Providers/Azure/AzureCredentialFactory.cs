using Azure.Core;
using Azure.Identity;

namespace ReleaseLens.Llm.Providers.Azure;

/// <summary>
/// Picks the credential by configuration. <c>DefaultAzureCredential</c> is deliberately not an
/// option: its chain can pick up the wrong account on a machine signed in to more than one
/// tenant, and a wrong identity that still gets a token looks like a working system.
/// </summary>
public static class AzureCredentialFactory
{
    public static TokenCredential Create(AzureOpenAiOptions options) => options.Credential switch
    {
        AzureCredentialKind.ManagedIdentity => new ManagedIdentityCredential(ManagedIdentityOptions(options)),
        AzureCredentialKind.AzureCli => CreateAzureCli(options),
        _ => throw new InvalidOperationException(
            $"AzureOpenAi:Credential '{options.Credential}' is not one of {string.Join(", ", Enum.GetNames<AzureCredentialKind>())}.")
    };

    /// <summary>
    /// The options the managed-identity credential is built with. Internal so the retry cap can
    /// be tested: a built credential does not expose its options.
    /// </summary>
    internal static ManagedIdentityCredentialOptions ManagedIdentityOptions(AzureOpenAiOptions options)
    {
        // FromUserAssignedClientId accepts an empty string, and without an ID the credential
        // asks for a system-assigned identity, which this app does not have. Either way the
        // failure would surface at the first query instead of here, where it is clear.
        if (string.IsNullOrWhiteSpace(options.ClientId))
        {
            throw new InvalidOperationException(
                "AzureOpenAi:Credential is ManagedIdentity but no client ID is set. "
                + "The container must provide AZURE_CLIENT_ID for its user-assigned identity.");
        }

        var credentialOptions = new ManagedIdentityCredentialOptions(
            ManagedIdentityId.FromUserAssignedClientId(options.ClientId));

        // Azure.Core's defaults here are three retries with exponential backoff from 0.8 s: a
        // wait the per-query 429 budget knows nothing about. One quick retry covers a blip;
        // anything longer is an outage, and the query degrades. Whether MSAL's own
        // managed-identity retry adds to this is unverified.
        credentialOptions.Retry.MaxRetries = 1;
        credentialOptions.Retry.Mode = RetryMode.Fixed;
        credentialOptions.Retry.Delay = TimeSpan.FromMilliseconds(200);
        credentialOptions.Retry.MaxDelay = TimeSpan.FromMilliseconds(200);

        return credentialOptions;
    }

    private static AzureCliCredential CreateAzureCli(AzureOpenAiOptions options)
    {
        // Without a tenant, az issues the token from whichever tenant it is signed in to, which
        // on a machine signed in to more than one can be the wrong one, and that shows only when
        // the first query is refused. A missing setting here fails at startup instead.
        if (string.IsNullOrWhiteSpace(options.TenantId))
        {
            throw new InvalidOperationException(
                "AzureOpenAi:Credential is AzureCli but AzureOpenAi:TenantId is not set. "
                + "Set it to the tenant of the Azure OpenAI account.");
        }

        return new AzureCliCredential(new AzureCliCredentialOptions { TenantId = options.TenantId });
    }
}
