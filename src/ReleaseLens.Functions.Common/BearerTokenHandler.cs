using System.Net.Http.Headers;
using Azure.Core;
using Azure.Identity;

namespace ReleaseLens.Functions.Common;

/// <summary>
/// Attaches an Entra bearer token for one scope to every request, and keeps the token until five
/// minutes before it expires. Hold one per credential-and-scope for the life of the process: the
/// cache is on the instance, so a handler that <c>IHttpClientFactory</c> recycles starts empty.
/// </summary>
/// <remarks>
/// Deliberately not <c>ReleaseLens.Llm</c>'s <c>EntraTokenHandler</c>: that project pulls in the ONNX
/// runtime, the embedding model and Npgsql, which neither Function app needs.
/// </remarks>
public sealed class BearerTokenHandler(TokenCredential credential, string scope, TimeProvider time)
    : DelegatingHandler
{
    /// <summary>The token audience for Azure OpenAI's v1 API.</summary>
    public const string OpenAiScope = "https://ai.azure.com/.default";

    /// <summary>The token audience for Azure AI Search.</summary>
    public const string SearchScope = "https://search.azure.com/.default";

    public static readonly TimeSpan RefreshBeforeExpiry = TimeSpan.FromMinutes(5);

    private readonly TokenRequestContext _context = new([scope]);
    private readonly SemaphoreSlim _refresh = new(1, 1);

    // A reference rather than the AccessToken struct, so a reader on another thread sees either the
    // old token or the new one, never half of each.
    private volatile CachedToken? _cached;

    protected override async Task<HttpResponseMessage> SendAsync(
        HttpRequestMessage request, CancellationToken cancellationToken)
    {
        string token;
        try
        {
            token = await GetTokenAsync(cancellationToken);
        }
        catch (AuthenticationFailedException failure)
        {
            // As an HttpRequestException it reads as any other failure to reach the service, and the
            // queue's retry or the tool's error handling treats it the same way.
            throw new HttpRequestException($"Could not get an Entra token: {failure.Message}", failure);
        }

        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
        return await base.SendAsync(request, cancellationToken);
    }

    private ValueTask<string> GetTokenAsync(CancellationToken cancellationToken)
    {
        var cached = _cached;
        return cached is not null && time.GetUtcNow() < cached.RefreshAt
            ? ValueTask.FromResult(cached.Token)
            : new ValueTask<string>(RefreshAsync(cancellationToken));
    }

    private async Task<string> RefreshAsync(CancellationToken cancellationToken)
    {
        await _refresh.WaitAsync(cancellationToken);
        try
        {
            // A caller that queued behind the refresh finds the new token here instead of asking the
            // credential a second time.
            var cached = _cached;
            if (cached is not null && time.GetUtcNow() < cached.RefreshAt)
            {
                return cached.Token;
            }

            var token = await credential.GetTokenAsync(_context, cancellationToken);
            _cached = new CachedToken(token.Token, token.ExpiresOn - RefreshBeforeExpiry);
            return token.Token;
        }
        finally
        {
            _refresh.Release();
        }
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            _refresh.Dispose();
        }

        base.Dispose(disposing);
    }

    private sealed record CachedToken(string Token, DateTimeOffset RefreshAt);
}
