using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.Extensions.Logging;

namespace ReleaseLens.Llm.Providers.Azure;

/// <summary>
/// Azure OpenAI through its v1 endpoint, which speaks the OpenAI chat-completions wire format,
/// so the request body and the response go through <see cref="OpenAiWireFormat"/> exactly as
/// OpenAI's do. What is Azure's own lives here: the deployment name in <c>model</c>, the
/// filtered-prompt 400, and the filter-did-not-run marker on a 200.
/// </summary>
/// <remarks>
/// Authentication is not done here. <see cref="EntraTokenHandler"/> on this provider's
/// <see cref="HttpClient"/> attaches the Entra token, and no API key header is ever set,
/// because the account has key authentication turned off.
/// </remarks>
public sealed class AzureOpenAiChatProvider : IPricedChatProvider
{
    private readonly HttpClient _client;
    private readonly AzureOpenAiOptions _options;
    private readonly TimeProvider _time;
    private readonly ILogger<AzureOpenAiChatProvider> _logger;

    public string Name => "azure-openai";

    public PricingIdentity Pricing { get; }

    public AzureOpenAiChatProvider(
        HttpClient client, AzureOpenAiOptions options, TimeProvider time, ILogger<AzureOpenAiChatProvider> logger)
    {
        _client = client;
        _options = options;
        _time = time;
        _logger = logger;

        Pricing = new PricingIdentity("azure-openai", options.Model, options.ModelVersion, options.DeploymentType);

        // Without the trailing slash, "chat/completions" resolves against ".../openai/" and the
        // "v1" segment is silently dropped.
        _client.BaseAddress ??= new Uri(options.BaseUrl.EndsWith('/') ? options.BaseUrl : options.BaseUrl + "/");
    }

    public async Task<ChatResponse> CompleteAsync(ChatRequest request, CancellationToken cancellationToken)
    {
        // The v1 endpoint takes no api-version query parameter, and its "model" is the
        // deployment name, never the model name.
        var payload = OpenAiWireFormat.BuildPayload(request, _options.Deployment);

        var response = await ProviderHttp.SendAsync(
            () => _client.PostAsJsonAsync("chat/completions", payload, ProviderHttp.JsonOptions, cancellationToken),
            Name, cancellationToken);

        using (response)
        {
            if (response.StatusCode == HttpStatusCode.BadRequest
                && IsFilteredPrompt(await response.Content.ReadAsStringAsync(cancellationToken)))
            {
                // A rejected prompt produced no completion, so there is no usage to report.
                throw new ContentFilteredException(Name, ContentFilterStage.Prompt, TokenUsage.Zero, Pricing);
            }

            // The body is buffered, so ProviderHttp can read it again for its message.
            await ProviderHttp.ThrowIfNotSuccessAsync(response, Name, cancellationToken);

            using var document = await JsonDocument.ParseAsync(
                await response.Content.ReadAsStreamAsync(cancellationToken), cancellationToken: cancellationToken);

            var answer = OpenAiWireFormat.Parse(document.RootElement, Name, _options.Deployment, Pricing);

            if (TryGetFilterError(document.RootElement, out var filterError))
            {
                // The filter did not run on this completion. Nothing was filtered, so the answer
                // stands, but it went out unchecked, which is worth a record.
                _logger.LogInformation(
                    "{Provider} content filter did not run on the completion; returning the answer. Filter error: {FilterError}",
                    Name, filterError);
            }

            return answer;
        }
    }

    /// <summary>
    /// A filtered prompt is a 400 whose <c>code</c> is <c>content_filter</c>, either inside an
    /// <c>error</c> envelope or at the top level. Which of the two the v1 endpoint sends is
    /// unverified (the only documented example is the legacy, enveloped one), so both are
    /// accepted. <c>innererror</c> and <c>inner_error</c> are diagnostics only and never read.
    /// </summary>
    private static bool IsFilteredPrompt(string body)
    {
        JsonDocument document;
        try
        {
            document = JsonDocument.Parse(body);
        }
        catch (JsonException)
        {
            return false;
        }

        using (document)
        {
            var root = document.RootElement;

            return IsContentFilterCode(root)
                || (root.ValueKind == JsonValueKind.Object
                    && root.TryGetProperty("error", out var error)
                    && IsContentFilterCode(error));
        }
    }

    private static bool IsContentFilterCode(JsonElement element)
        => element.ValueKind == JsonValueKind.Object
           && element.TryGetProperty("code", out var code)
           && code.ValueKind == JsonValueKind.String
           && code.ValueEquals("content_filter");

    /// <summary>
    /// <c>content_filter_results.error</c> on a choice means the filter failed to run, not
    /// that it blocked anything.
    /// </summary>
    private static bool TryGetFilterError(JsonElement root, out string filterError)
    {
        // Parse has already proved choices is a non-empty array.
        if (root.GetProperty("choices")[0] is { ValueKind: JsonValueKind.Object } choice
            && choice.TryGetProperty("content_filter_results", out var results)
            && results.ValueKind == JsonValueKind.Object
            && results.TryGetProperty("error", out var error)
            && error.ValueKind == JsonValueKind.Object)
        {
            filterError = error.GetRawText();
            return true;
        }

        filterError = string.Empty;
        return false;
    }
}
