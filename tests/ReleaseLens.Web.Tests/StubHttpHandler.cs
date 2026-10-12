using System.Net;

namespace ReleaseLens.Web.Tests;

public sealed class StubHttpHandler : HttpMessageHandler
{
    public List<(string Method, Uri? Uri, string? Authorization, string? Body)> Requests { get; } = [];
    public Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> Reply { get; set; } = (_, _) => Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent("{\"choices\":[{\"message\":{\"content\":\"Safe answer\"}}]}") });
    protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
    {
        Requests.Add((request.Method.Method, request.RequestUri, request.Headers.Authorization?.Scheme, request.Content is null ? null : await request.Content.ReadAsStringAsync(cancellationToken)));
        return await Reply(request, cancellationToken);
    }
}
