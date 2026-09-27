namespace ReleaseLens.Llm.Providers.Azure;

public enum AzureCredentialKind { AzureCli, ManagedIdentity }

public sealed class AzureOpenAiOptions
{
    public const string SectionName = "AzureOpenAi";

    /// <summary>The v1 endpoint, e.g. https://&lt;subdomain&gt;.openai.azure.com/openai/v1/.</summary>
    public string BaseUrl { get; set; } = string.Empty;

    public string Deployment { get; set; } = string.Empty;

    /// <summary>
    /// The pricing identity only, never sent: Azure routes on the deployment name, and the
    /// rate is looked up by model, version and deployment type.
    /// </summary>
    public string Model { get; set; } = "gpt-4.1-mini";

    public string ModelVersion { get; set; } = "2025-04-14";

    /// <summary>
    /// Global Standard, because the subscription has no regional Standard quota for this model;
    /// it may process a prompt in any Azure region.
    /// </summary>
    public string DeploymentType { get; set; } = "GlobalStandard";

    /// <summary>
    /// Configurable because an API Management gateway in front of the account would change
    /// it, as it would the base URL.
    /// </summary>
    public string TokenScope { get; set; } = "https://ai.azure.com/.default";

    public AzureCredentialKind Credential { get; set; } = AzureCredentialKind.AzureCli;

    /// <summary>The tenant <see cref="AzureCredentialKind.AzureCli"/> asks for its token in.</summary>
    public string? TenantId { get; set; }

    /// <summary>
    /// The user-assigned identity's client ID, for <see cref="AzureCredentialKind.ManagedIdentity"/>.
    /// In the container it comes from AZURE_CLIENT_ID.
    /// </summary>
    public string? ClientId { get; set; }
}
