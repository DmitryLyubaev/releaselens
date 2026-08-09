using System.Net;
using System.Text.Json;

namespace ReleaseLens.Llm.Providers;

/// <summary>
/// Transport and status-code handling shared by every HTTP chat provider. Deliberately a
/// static helper rather than a base class: the adapters differ only in payload shape, and
/// inheritance would buy nothing over two implementations while costing readability.
/// </summary>
internal static class ProviderHttp
{
    public static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    /// <summary>
    /// Wraps transport failures and timeouts as ProviderUnavailableException so the fallback
    /// chain can retry elsewhere. A genuine caller cancellation is deliberately left unmapped,
    /// so it stays distinguishable from a timeout.
    /// </summary>
    public static async Task<HttpResponseMessage> SendAsync(
        Func<Task<HttpResponseMessage>> send, string providerName, CancellationToken cancellationToken)
    {
        try
        {
            return await send();
        }
        catch (HttpRequestException transport)
        {
            throw new ProviderUnavailableException(providerName, $"{providerName} transport failure.", transport);
        }
        catch (TaskCanceledException timeout) when (!cancellationToken.IsCancellationRequested)
        {
            throw new ProviderUnavailableException(providerName, $"{providerName} request timed out.", timeout);
        }
    }

    /// <summary>
    /// Maps a non-success response onto the two exception types the fallback chain
    /// distinguishes.
    /// </summary>
    /// <remarks>
    /// <b>Unavailable</b> — 429, 5xx, and also <b>401/403/408</b>. A missing or wrong provider
    /// credential makes that provider unusable, which is operationally identical to it being
    /// down: the other provider may well have a valid key, so falling through is exactly right.
    /// Classifying 401 as a caller bug instead produced an unhandled exception that escaped
    /// the fallback chain AND the agent's degraded-mode catch, so a service running without an
    /// ANTHROPIC_API_KEY answered every query with an HTTP 500 and a stack trace — precisely
    /// the failure the spec's "both providers down returns evidence unsynthesised" rule exists
    /// to prevent. Nothing is hidden by this: the degraded response names every provider tried
    /// and why it failed.
    ///
    /// <b>Caller bug</b> — everything else, chiefly 400 and 422. A malformed request is ours;
    /// retrying it against a second provider would hide it and spend money doing so.
    /// </remarks>
    public static async Task ThrowIfNotSuccessAsync(
        HttpResponseMessage response, string providerName, CancellationToken cancellationToken)
    {
        if (response.IsSuccessStatusCode)
        {
            return;
        }

        var body = await response.Content.ReadAsStringAsync(cancellationToken);
        var status = (int)response.StatusCode;

        if (response.StatusCode is HttpStatusCode.TooManyRequests
                or HttpStatusCode.Unauthorized
                or HttpStatusCode.Forbidden
                or HttpStatusCode.RequestTimeout
            || status >= 500)
        {
            throw new ProviderUnavailableException(providerName, $"{providerName} returned {status}: {body}");
        }

        throw new InvalidOperationException($"{providerName} rejected the request ({status}): {body}");
    }

    public static int ReadInt(JsonElement element, string name)
        => element.ValueKind == JsonValueKind.Object
           && element.TryGetProperty(name, out var value)
           && value.ValueKind == JsonValueKind.Number
            ? value.GetInt32()
            : 0;
}
