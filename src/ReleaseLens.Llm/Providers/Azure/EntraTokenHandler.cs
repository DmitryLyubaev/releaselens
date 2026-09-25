using System.Net.Http.Headers;
using Azure.Identity;

namespace ReleaseLens.Llm.Providers.Azure;

/// <summary>
/// Attaches the Entra bearer token to every request on the Azure provider's client.
/// </summary>
public sealed class EntraTokenHandler(EntraTokenCache cache) : DelegatingHandler
{
    protected override async Task<HttpResponseMessage> SendAsync(
        HttpRequestMessage request, CancellationToken cancellationToken)
    {
        string token;
        try
        {
            token = await cache.GetTokenAsync(cancellationToken);
        }
        catch (AuthenticationFailedException failure)
        {
            // No token leaves the provider as unusable as a 401 would. As an
            // HttpRequestException it becomes ProviderUnavailableException in ProviderHttp,
            // so the query degrades instead of escaping as an HTTP 500.
            throw new HttpRequestException($"Could not get an Entra token: {failure.Message}", failure);
        }

        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
        return await base.SendAsync(request, cancellationToken);
    }
}
