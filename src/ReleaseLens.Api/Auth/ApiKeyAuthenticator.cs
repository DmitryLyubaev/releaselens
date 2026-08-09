using ReleaseLens.Storage.Repositories;

namespace ReleaseLens.Api.Auth;

/// <summary>
/// Resolves the tenant for a request from its <c>X-Api-Key</c> header.
/// </summary>
/// <remarks>
/// Extracted rather than inlined in the route handler because this is the application's
/// one privileged, RLS-bypassing lookup. Keeping it in a single named place means a second
/// endpoint cannot quietly reimplement it slightly differently, and the header name is not
/// duplicated as a string literal across handlers.
/// </remarks>
public sealed class ApiKeyAuthenticator(ApiKeyRepository apiKeys)
{
    public const string HeaderName = "X-Api-Key";

    public Task<Guid?> ResolveTenantAsync(HttpContext context, CancellationToken cancellationToken)
        => apiKeys.ResolveTenantAsync(context.Request.Headers[HeaderName].ToString(), cancellationToken);
}
