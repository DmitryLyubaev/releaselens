using System.Globalization;
using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging;

namespace ReleaseLens.Llm.Providers.Azure;

/// <summary>
/// Azure OpenAI through its v1 endpoint, which speaks the OpenAI chat-completions wire format,
/// so the request body and the response go through <see cref="OpenAiWireFormat"/> exactly as
/// OpenAI's do. What is Azure's own lives here: the deployment name in <c>model</c>, the
/// filtered-prompt 400, the filter-did-not-run marker on a 200, and the bounded wait on a 429.
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

        var response = await SendAsync(payload, request.Context, cancellationToken);

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
    /// Sends the request, and on a 429 whose advised wait fits what is left of the query's
    /// budget, waits that long and sends it once more. Every other response, including a 429
    /// that cannot be waited out and a second 429, is returned for the caller to map as usual,
    /// so it becomes <see cref="ProviderUnavailableException"/> and the chain falls through.
    /// </summary>
    /// <remarks>
    /// The budget belongs to the query, not to this provider, which every concurrent query
    /// shares. Without a <see cref="QueryContext"/> there is no budget, so the call never waits.
    /// </remarks>
    private async Task<HttpResponseMessage> SendAsync(
        JsonObject payload, QueryContext? context, CancellationToken cancellationToken)
    {
        var response = await PostAsync(payload, cancellationToken);

        if (response.StatusCode != HttpStatusCode.TooManyRequests
            || context is null
            || ReadAdvisedWait(response) is not { } wait
            || !context.TrySpendWait(wait))
        {
            return response;
        }

        response.Dispose();

        _logger.LogInformation(
            "{Provider} returned 429; waiting the advised {WaitMs} ms before its one retry, {RemainingMs} ms of the query's wait budget left",
            Name, wait.TotalMilliseconds, context.RateLimitWaitRemaining.TotalMilliseconds);

        await Task.Delay(wait, _time, cancellationToken);

        return await PostAsync(payload, cancellationToken);
    }

    private Task<HttpResponseMessage> PostAsync(JsonObject payload, CancellationToken cancellationToken)
        => ProviderHttp.SendAsync(
            () => _client.PostAsJsonAsync("chat/completions", payload, ProviderHttp.JsonOptions, cancellationToken),
            Name, cancellationToken);

    /// <summary>
    /// The wait a 429 advises: <c>retry-after-ms</c> in milliseconds when it is usable,
    /// otherwise <c>retry-after</c> in whole seconds, otherwise null. Microsoft's Azure OpenAI
    /// header table lists <c>retry-after-ms</c> and not <c>retry-after</c>, so the second is only
    /// a fallback, and its HTTP-date form is ignored.
    /// </summary>
    /// <remarks>
    /// A usable value is one header value that is a plain non-negative integer that fits an
    /// <see cref="int"/>. Anything else (a decimal, a sign, whitespace, a unit, a list, or a
    /// number past <see cref="int.MaxValue"/>) is treated as absent rather than rounded, trimmed
    /// or clamped, so the call falls through at once. A well-formed value too long for the
    /// budget falls through as well, because it does not fit.
    /// </remarks>
    private static TimeSpan? ReadAdvisedWait(HttpResponseMessage response)
    {
        if (TryReadPlainInteger(response, "retry-after-ms", out var milliseconds))
        {
            return TimeSpan.FromMilliseconds(milliseconds);
        }

        if (TryReadPlainInteger(response, "retry-after", out var seconds))
        {
            return TimeSpan.FromSeconds(seconds);
        }

        return null;
    }

    private static bool TryReadPlainInteger(HttpResponseMessage response, string header, out int value)
    {
        value = 0;

        // NonValidated gives the header text as received; the typed Headers.RetryAfter would
        // also accept an HTTP date. NumberStyles.None allows digits only: no sign, decimal
        // point or whitespace.
        return response.Headers.NonValidated.TryGetValues(header, out var values)
            && values.Count == 1
            && int.TryParse(values.Single(), NumberStyles.None, CultureInfo.InvariantCulture, out value);
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
