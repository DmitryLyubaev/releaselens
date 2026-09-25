using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using Azure.Core;

namespace ReleaseLens.Llm.Tests;

/// <summary>
/// Replays scripted tokens or failures in order and records the scopes each call asked for.
/// Setting <see cref="Gate"/> holds every call open until the test releases it, which is how
/// a test puts two callers at the refresh point at the same moment.
/// </summary>
internal sealed class FakeTokenCredential : TokenCredential
{
    private readonly Queue<Func<AccessToken>> _script = new();
    private readonly List<string[]> _requestedScopes = [];
    private readonly Lock _lock = new();

    public TaskCompletionSource? Gate { get; set; }

    public IReadOnlyList<string[]> RequestedScopes
    {
        get
        {
            lock (_lock)
            {
                return [.. _requestedScopes];
            }
        }
    }

    public int Calls => RequestedScopes.Count;

    public FakeTokenCredential Returns(string token, DateTimeOffset expiresOn)
    {
        _script.Enqueue(() => new AccessToken(token, expiresOn));
        return this;
    }

    public FakeTokenCredential Throws(Exception failure)
    {
        _script.Enqueue(() => throw failure);
        return this;
    }

    public override async ValueTask<AccessToken> GetTokenAsync(
        TokenRequestContext requestContext, CancellationToken cancellationToken)
    {
        Func<AccessToken>? next;
        lock (_lock)
        {
            _requestedScopes.Add(requestContext.Scopes);
            if (!_script.TryDequeue(out next))
            {
                throw new InvalidOperationException(
                    $"The credential was asked for token number {_requestedScopes.Count}, but only {_requestedScopes.Count - 1} were scripted.");
            }
        }

        if (Gate is { } gate)
        {
            await gate.Task.WaitAsync(cancellationToken);
        }

        return next();
    }

    public override AccessToken GetToken(TokenRequestContext requestContext, CancellationToken cancellationToken)
        => throw new NotSupportedException("The token cache must only use the async path.");
}
