using System.Net;
using System.Net.Http.Headers;
using System.Text;

namespace ReleaseLens.Functions.Tests;

/// <summary>One request as it left the client, with its body read before the client could dispose it.</summary>
internal sealed record SentRequest(
    HttpMethod Method, Uri Uri, AuthenticationHeaderValue? Authorization, string Body);

/// <summary>Records every request and replays canned responses in order. No network.</summary>
internal sealed class StubHttpHandler : HttpMessageHandler
{
    private readonly Queue<HttpResponseMessage> _responses = new();

    public List<SentRequest> Requests { get; } = [];

    public StubHttpHandler Enqueue(HttpStatusCode status, string body)
    {
        _responses.Enqueue(new HttpResponseMessage(status)
        {
            Content = new StringContent(body, Encoding.UTF8, "application/json")
        });
        return this;
    }

    public StubHttpHandler EnqueueJson(string body) => Enqueue(HttpStatusCode.OK, body);

    protected override async Task<HttpResponseMessage> SendAsync(
        HttpRequestMessage request, CancellationToken cancellationToken)
    {
        var body = request.Content is null ? "" : await request.Content.ReadAsStringAsync(cancellationToken);
        Requests.Add(new SentRequest(request.Method, request.RequestUri!, request.Headers.Authorization, body));

        if (_responses.Count == 0)
        {
            throw new InvalidOperationException(
                $"Unexpected request to {request.RequestUri}: no response was enqueued.");
        }

        return _responses.Dequeue();
    }
}
