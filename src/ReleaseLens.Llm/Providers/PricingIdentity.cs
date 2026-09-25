namespace ReleaseLens.Llm.Providers;

/// <summary>
/// What a provider's calls are billed as. Pricing reads this, never the <c>model</c> string a
/// response carries: that string is whatever the endpoint chose to echo (a versioned snapshot
/// name, or an Azure deployment name), and pricing by it would price a versioned snapshot name
/// at $0 (F2).
/// </summary>
/// <param name="Unpriced">
/// Set only for runtimes that do not bill per token, such as Ollama behind the OpenAI-compatible
/// provider. It is the one way an identity is allowed to cost nothing.
/// </param>
public sealed record PricingIdentity(
    string Provider, string Model, string? Version = null, string? DeploymentType = null, bool Unpriced = false);

/// <summary>A provider that knows the identity its calls are billed under.</summary>
public interface IPricedChatProvider : IChatProvider
{
    PricingIdentity Pricing { get; }
}
