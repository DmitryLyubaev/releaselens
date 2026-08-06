using System;
using System.Collections.Generic;
using System.Net;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;

namespace ReleaseLens.Ingestion.Tests;

/// <summary>
/// Records every request and replays canned responses in order. The GitHub client is
/// tested entirely against this — no network, no fixtures that rot when GitHub changes.
/// </summary>
public sealed class StubHttpMessageHandler : HttpMessageHandler
{
    private readonly Queue<HttpResponseMessage> _responses = new();

    public List<HttpRequestMessage> Requests { get; } = [];

    public StubHttpMessageHandler Enqueue(HttpStatusCode status, string body, Action<HttpResponseMessage>? configure = null)
    {
        var response = new HttpResponseMessage(status)
        {
            Content = new StringContent(body, System.Text.Encoding.UTF8, "application/json")
        };
        configure?.Invoke(response);
        _responses.Enqueue(response);
        return this;
    }

    public StubHttpMessageHandler EnqueueJson(string body) => Enqueue(HttpStatusCode.OK, body);

    public StubHttpMessageHandler EnqueuePage(string body, string? nextLink)
        => Enqueue(HttpStatusCode.OK, body, response =>
        {
            if (nextLink is not null)
            {
                response.Headers.TryAddWithoutValidation("Link", $"<{nextLink}>; rel=\"next\"");
            }
        });

    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
    {
        Requests.Add(request);

        if (_responses.Count == 0)
        {
            throw new InvalidOperationException(
                $"Unexpected request to {request.RequestUri} — no response was enqueued.");
        }

        return Task.FromResult(_responses.Dequeue());
    }
}
