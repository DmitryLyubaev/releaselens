using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;

namespace ReleaseLens.Llm.Providers;

public sealed class OpenAiOptions
{
    public const string SectionName = "OpenAi";

    public string ApiKey { get; set; } = string.Empty;
    public string Model { get; set; } = "gpt-4o";

    /// <summary>
    /// Any endpoint speaking the OpenAI wire format. Point this at
    /// http://localhost:11434/v1/ for Ollama, or a vLLM or LM Studio address,
    /// and the whole query path runs with no data leaving the network.
    /// </summary>
    public string BaseUrl { get; set; } = "https://api.openai.com/v1/";

    public int MaxTokens { get; set; } = 2048;

    /// <summary>
    /// True for a local runtime that does not bill per token (Ollama, vLLM, LM Studio). It is
    /// the only way this provider's calls may cost $0; a hosted model with no rate fails the
    /// startup pricing check instead.
    /// </summary>
    public bool Unpriced { get; set; }
}

public sealed class OpenAiChatProvider : IPricedChatProvider
{
    private readonly HttpClient _client;
    private readonly OpenAiOptions _options;

    public string Name => "openai";

    public PricingIdentity Pricing { get; }

    public OpenAiChatProvider(HttpClient client, OpenAiOptions options)
    {
        _client = client;
        _options = options;
        Pricing = new PricingIdentity("openai", options.Model, Unpriced: options.Unpriced);

        _client.BaseAddress ??= new Uri(options.BaseUrl);

        // Local runtimes ignore the header, and sending "Bearer " with an empty
        // value can make some of them reject the request outright.
        if (!string.IsNullOrEmpty(options.ApiKey))
        {
            _client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", options.ApiKey);
        }
    }

    public async Task<ChatResponse> CompleteAsync(ChatRequest request, CancellationToken cancellationToken)
    {
        var payload = OpenAiWireFormat.BuildPayload(request, _options.Model);

        var response = await ProviderHttp.SendAsync(
            () => _client.PostAsJsonAsync("chat/completions", payload, ProviderHttp.JsonOptions, cancellationToken),
            Name, cancellationToken);

        using (response)
        {
            await ProviderHttp.ThrowIfNotSuccessAsync(response, Name, cancellationToken);

            using var document = await JsonDocument.ParseAsync(
                await response.Content.ReadAsStreamAsync(cancellationToken), cancellationToken: cancellationToken);

            return OpenAiWireFormat.Parse(document.RootElement, Name, _options.Model, Pricing);
        }
    }
}
