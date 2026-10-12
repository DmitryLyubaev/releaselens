using System.Net;
using System.Net.Http.Headers;
using Microsoft.Extensions.Time.Testing;
using ReleaseLens.Web;

namespace ReleaseLens.Web.Tests;

public sealed class GatewayClientTests
{
    [Theory]
    [InlineData(302, QuestionKind.Unavailable)]
    [InlineData(202, QuestionKind.Unavailable)]
    [InlineData(401, QuestionKind.SignInRequired)]
    [InlineData(403, QuestionKind.Forbidden)]
    [InlineData(429, QuestionKind.RateLimited)]
    [InlineData(500, QuestionKind.Unavailable)]
    [InlineData(503, QuestionKind.Unavailable)]
    public async Task RefusalDoesNotReadBodyOrRetry(int status, QuestionKind expected)
    {
        var content = new UnreadableContent();
        var (client, stub, _) = Create(new HttpResponseMessage((HttpStatusCode)status) { Content = content });
        var result = await client.AskAsync("question", "private-test-bearer", TestContext.Current.CancellationToken);
        Assert.Equal(expected, result.Kind);
        Assert.Equal(0, content.ReadAttempts);
        Assert.Single(stub.Requests);
    }

    [Theory]
    [InlineData("{\"choices\":[{\"message\":{\"content\":\"answer\"}}]}", QuestionKind.Success)]
    [InlineData("{\"choices\":[{\"message\":{\"content\":\" \"}}]}", QuestionKind.Unavailable)]
    [InlineData("{\"choices\":[{\"message\":{\"content\":null}}]}", QuestionKind.Unavailable)]
    [InlineData("{}", QuestionKind.Unavailable)]
    [InlineData("not-json", QuestionKind.Unavailable)]
    public async Task OnlyNonemptyValidAnswerSucceeds(string body, QuestionKind expected)
    {
        var (client, _, _) = Create(new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent(body) });
        Assert.Equal(expected, (await client.AskAsync("question", "private-test-bearer", TestContext.Current.CancellationToken)).Kind);
    }

    [Fact]
    public async Task OversizeSuccessBodyIsRejected()
    {
        var (client, _, _) = Create(new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent(new string('x', 1024 * 1024 + 1)) });
        Assert.Equal(QuestionKind.Unavailable, (await client.AskAsync("question", "private-test-bearer", TestContext.Current.CancellationToken)).Kind);
    }

    [Theory]
    [InlineData("0", 0)]
    [InlineData("120", 120)]
    [InlineData("3600", 3600)]
    [InlineData("3601", null)]
    [InlineData("-1", null)]
    [InlineData("invalid", null)]
    [InlineData("Mon, 12 Oct 2026 00:02:00 GMT", 120)]
    [InlineData("Mon, 12 Oct 2026 02:00:00 GMT", null)]
    public async Task RetryAfterIsBounded(string value, int? expected)
    {
        var response = new HttpResponseMessage(HttpStatusCode.TooManyRequests) { Content = new UnreadableContent() };
        response.Headers.TryAddWithoutValidation("Retry-After", value);
        var (client, _, _) = Create(response);
        Assert.Equal(expected, (await client.AskAsync("question", "private-test-bearer", TestContext.Current.CancellationToken)).RetryAfterSeconds);
    }

    [Fact]
    public async Task AuthorizationIsPerRequestAndNeverReused()
    {
        var stub = new StubHttpHandler();
        using var http = new HttpClient(stub);
        var client = new GatewayClient(http, Fixtures.Options(), TimeProvider.System);
        await client.AskAsync("question", "first-private", TestContext.Current.CancellationToken);
        await client.AskAsync("question", "second-private", TestContext.Current.CancellationToken);
        Assert.True(stub.Bearers.SequenceEqual(new[] { "first-private", "second-private" }));
        Assert.Null(http.DefaultRequestHeaders.Authorization);
    }

    [Fact]
    public async Task TransportTimeoutIsUnavailable()
    {
        var stub = new StubHttpHandler { Reply = (_, _) => throw new TaskCanceledException() };
        var client = new GatewayClient(new HttpClient(stub), Fixtures.Options(), TimeProvider.System);
        Assert.Equal(QuestionKind.Unavailable, (await client.AskAsync("question", "private-test-bearer", TestContext.Current.CancellationToken)).Kind);
    }

    [Fact]
    public async Task CallerCancellationIsRethrown()
    {
        using var cts = CancellationTokenSource.CreateLinkedTokenSource(TestContext.Current.CancellationToken);
        var stub = new StubHttpHandler { Reply = (_, ct) => { cts.Cancel(); ct.ThrowIfCancellationRequested(); throw new InvalidOperationException(); } };
        var client = new GatewayClient(new HttpClient(stub), Fixtures.Options(), TimeProvider.System);
        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => client.AskAsync("question", "private-test-bearer", cts.Token));
    }

    [Fact]
    public async Task LinkedTimeoutCoversResponseHeaders()
    {
        var clock = new FakeTimeProvider();
        var entered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var stub = new StubHttpHandler { Reply = async (_, ct) => { entered.SetResult(); await Task.Delay(Timeout.Infinite, ct); return new HttpResponseMessage(HttpStatusCode.OK); } };
        var client = new GatewayClient(new HttpClient(stub) { Timeout = Timeout.InfiniteTimeSpan }, Fixtures.Options(), clock);
        var task = client.AskAsync("question", "private-test-bearer", TestContext.Current.CancellationToken);
        await entered.Task.WaitAsync(TestContext.Current.CancellationToken);
        clock.Advance(TimeSpan.FromSeconds(61));
        Assert.Equal(QuestionKind.Unavailable, (await task).Kind);
    }

    [Fact]
    public async Task UnknownLengthSuccessBodyIsBounded()
    {
        var (client, _, _) = Create(new HttpResponseMessage(HttpStatusCode.OK) { Content = new StreamContent(new UnknownLengthStream(new string('x', 1024 * 1024 + 1))) });
        Assert.Equal(QuestionKind.Unavailable, (await client.AskAsync("question", "private-test-bearer", TestContext.Current.CancellationToken)).Kind);
    }

    [Fact]
    public async Task LinkedTimeoutCoversSuccessBodyReading()
    {
        var clock = new FakeTimeProvider();
        var stream = new WaitingStream();
        var stub = new StubHttpHandler { Reply = (_, _) => Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = new StreamContent(stream) }) };
        var client = new GatewayClient(new HttpClient(stub) { Timeout = Timeout.InfiniteTimeSpan }, Fixtures.Options(), clock);
        var task = client.AskAsync("question", "private-test-bearer", TestContext.Current.CancellationToken);
        await stream.Entered.Task.WaitAsync(TestContext.Current.CancellationToken);
        clock.Advance(TimeSpan.FromSeconds(61));
        Assert.Equal(QuestionKind.Unavailable, (await task).Kind);
    }

    private static (GatewayClient Client, StubHttpHandler Stub, FakeTimeProvider Clock) Create(HttpResponseMessage response)
    {
        var stub = new StubHttpHandler { Reply = (_, _) => Task.FromResult(response) };
        var clock = new FakeTimeProvider(new DateTimeOffset(2026, 10, 12, 0, 0, 0, TimeSpan.Zero));
        return (new GatewayClient(new HttpClient(stub), Fixtures.Options(), clock), stub, clock);
    }
}

internal sealed class UnreadableContent : HttpContent
{
    public int ReadAttempts { get; private set; }
    protected override Task SerializeToStreamAsync(Stream stream, TransportContext? context) { ReadAttempts++; throw new InvalidOperationException("Refusal body must not be read"); }
    protected override bool TryComputeLength(out long length) { length = 0; return false; }
}

internal sealed class UnknownLengthStream(string value) : MemoryStream(System.Text.Encoding.UTF8.GetBytes(value))
{
    public override bool CanSeek => false;
}

internal sealed class WaitingStream : Stream
{
    public TaskCompletionSource Entered { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
    public override bool CanRead => true;
    public override bool CanSeek => false;
    public override bool CanWrite => false;
    public override long Length => throw new NotSupportedException();
    public override long Position { get => throw new NotSupportedException(); set => throw new NotSupportedException(); }
    public override void Flush() => throw new NotSupportedException();
    public override int Read(byte[] buffer, int offset, int count) => throw new NotSupportedException();
    public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
    public override void SetLength(long value) => throw new NotSupportedException();
    public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();
    public override async ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken cancellationToken = default)
    {
        Entered.SetResult();
        await Task.Delay(Timeout.Infinite, cancellationToken);
        return 0;
    }
}
