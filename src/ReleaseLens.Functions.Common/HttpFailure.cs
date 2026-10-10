using System.Globalization;

namespace ReleaseLens.Functions.Common;

internal static class HttpFailure
{
    /// <summary>
    /// Throws for a non-success reply with the service and the status, and nothing the service said:
    /// a backend's error text can name an index, a deployment or a piece of the request, and these
    /// messages reach logs.
    /// </summary>
    public static void ThrowIfNotSuccess(HttpResponseMessage response, string service)
    {
        if (response.IsSuccessStatusCode)
        {
            return;
        }

        throw new HttpRequestException(
            $"{service} replied HTTP {((int)response.StatusCode).ToString(CultureInfo.InvariantCulture)}.",
            inner: null,
            statusCode: response.StatusCode);
    }
}
