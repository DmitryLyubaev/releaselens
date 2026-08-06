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
    /// 429 and 5xx become ProviderUnavailableException so the fallback chain tries the next
    /// provider. Every other non-success status becomes InvalidOperationException, because a
    /// malformed request is our bug — retrying it against a second provider would hide the bug
    /// and spend money doing so.
    /// </summary>
    public static async Task ThrowIfNotSuccessAsync(
        HttpResponseMessage response, string providerName, CancellationToken cancellationToken)
    {
        if (response.IsSuccessStatusCode)
        {
            return;
        }

        var body = await response.Content.ReadAsStringAsync(cancellationToken);
        var status = (int)response.StatusCode;

        if (response.StatusCode is HttpStatusCode.TooManyRequests || status >= 500)
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
