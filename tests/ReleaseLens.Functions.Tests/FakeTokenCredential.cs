using Azure.Core;

namespace ReleaseLens.Functions.Tests;

/// <summary>Replays scripted tokens in order and records the scopes each call asked for.</summary>
internal sealed class FakeTokenCredential : TokenCredential
{
    private readonly Queue<AccessToken> _script = new();

    public List<string[]> RequestedScopes { get; } = [];

    public FakeTokenCredential Returns(string token, DateTimeOffset expiresOn)
    {
        _script.Enqueue(new AccessToken(token, expiresOn));
        return this;
    }

    public override ValueTask<AccessToken> GetTokenAsync(
        TokenRequestContext requestContext, CancellationToken cancellationToken)
    {
        RequestedScopes.Add(requestContext.Scopes);
        return _script.TryDequeue(out var next)
            ? ValueTask.FromResult(next)
            : throw new InvalidOperationException(
                $"The credential was asked for token number {RequestedScopes.Count}, but only {RequestedScopes.Count - 1} were scripted.");
    }

    public override AccessToken GetToken(TokenRequestContext requestContext, CancellationToken cancellationToken)
        => throw new NotSupportedException("The handler must only use the async path.");
}
