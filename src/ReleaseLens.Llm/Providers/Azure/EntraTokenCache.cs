using Azure.Core;

namespace ReleaseLens.Llm.Providers.Azure;

/// <summary>
/// Holds the Entra token for the process. A singleton rather than state on the handler,
/// because <c>IHttpClientFactory</c> recycles handlers; and needed at all because
/// <c>AzureCliCredential</c> does not cache, so without it every agent iteration would start
/// an <c>az</c> process.
/// </summary>
public sealed class EntraTokenCache(TokenCredential credential, string scope, TimeProvider time)
{
    public static readonly TimeSpan RefreshBeforeExpiry = TimeSpan.FromMinutes(5);

    private readonly TokenRequestContext _context = new([scope]);
    private readonly SemaphoreSlim _refresh = new(1, 1);

    // A reference rather than the AccessToken struct, so a reader on another thread sees
    // either the old token or the new one, never half of each.
    private volatile CachedToken? _cached;

    public ValueTask<string> GetTokenAsync(CancellationToken cancellationToken)
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
            // A caller that queued behind the refresh finds the new token here instead of
            // asking the credential a second time.
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

    private sealed record CachedToken(string Token, DateTimeOffset RefreshAt);
}
