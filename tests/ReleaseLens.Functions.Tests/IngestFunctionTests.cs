using Azure;
using Microsoft.Extensions.Logging;
using ReleaseLens.Functions.Common;
using ReleaseLens.Functions.Ingest;

namespace ReleaseLens.Functions.Tests;

public class IngestFunctionTests
{
    private static CancellationToken Ct => TestContext.Current.CancellationToken;

    private sealed class FakeBlobs(Func<BlobRef, string> read) : IArtefactBlobs
    {
        public List<BlobRef> Reads { get; } = [];

        public Task<string> ReadTextAsync(BlobRef blob, CancellationToken cancellationToken)
        {
            Reads.Add(blob);
            return Task.FromResult(read(blob));
        }
    }

    /// <summary>Keeps everything a log call carries: the rendered message, each structured value, and the exception.</summary>
    private sealed class CapturingLogger : ILogger<IngestFunction>
    {
        public List<string> Entries { get; } = [];

        public IDisposable? BeginScope<TState>(TState state) where TState : notnull => null;

        public bool IsEnabled(LogLevel logLevel) => true;

        public void Log<TState>(
            LogLevel logLevel, EventId eventId, TState state, Exception? exception, Func<TState, Exception?, string> formatter)
        {
            Entries.Add(formatter(state, exception));
            if (state is IEnumerable<KeyValuePair<string, object?>> values)
            {
                Entries.AddRange(values.Select(pair => $"{pair.Key}={pair.Value}"));
            }

            if (exception is not null)
            {
                Entries.Add(exception.ToString());
            }
        }
    }

    private static IngestFunction Function(
        IArtefactBlobs blobs, StubHttpHandler stub, CapturingLogger? log = null) =>
        new(blobs, IngestFixtures.Ingest(stub), log ?? new CapturingLogger());

    [Fact]
    public async Task Ingest_ReadsTheBlobTheEventNames_ThenIngestsIt()
    {
        var issue = IngestFixtures.Issue();
        var count = IngestFixtures.ChunksOf(issue).Count;
        var blobs = new FakeBlobs(_ => ArtefactJson.Serialize(issue));
        var stub = IngestFixtures.StubFor(count);

        await Function(blobs, stub).Run(IngestFixtures.Base64(IngestFixtures.EventFor()), Ct);

        Assert.Equal(new BlobRef("artefacts-in", IngestFixtures.BlobName), Assert.Single(blobs.Reads));
        Assert.Equal(3, stub.Requests.Count);
    }

    [Fact]
    public async Task Ingest_ABlobThatIsGone_FailsTheAttempt()
    {
        // The event can outlive its blob: the queue then retries, and the message ends in poison.
        var blobs = new FakeBlobs(_ => throw new RequestFailedException(404, "The blob is gone.", "BlobNotFound", null));
        var stub = new StubHttpHandler();

        var failure = await Assert.ThrowsAsync<RequestFailedException>(
            () => Function(blobs, stub).Run(IngestFixtures.EventFor(), Ct));

        Assert.Equal(404, failure.Status);
        Assert.Empty(stub.Requests);
    }

    [Fact]
    public async Task Ingest_AnEventThatIsNotOurs_FailsBeforeTouchingTheBlobOrTheServices()
    {
        var blobs = new FakeBlobs(_ => throw new InvalidOperationException("must not be read"));
        var stub = new StubHttpHandler();

        await Assert.ThrowsAsync<InvalidArtefactException>(
            () => Function(blobs, stub).Run(IngestFixtures.EventFor(container: "other"), Ct));

        Assert.Empty(blobs.Reads);
        Assert.Empty(stub.Requests);
    }

    [Fact]
    public async Task Ingest_ABlobThatIsNotAnArtefact_FailsBeforeTouchingTheServices()
    {
        var blobs = new FakeBlobs(_ => """{"entityType":"commit"}""");
        var stub = new StubHttpHandler();

        await Assert.ThrowsAsync<InvalidArtefactException>(
            () => Function(blobs, stub).Run(IngestFixtures.EventFor(), Ct));

        Assert.Empty(stub.Requests);
    }

    [Fact]
    public async Task Ingest_TheLogHoldsNoArtefactText()
    {
        const string SecretTitle = "SECRET-TITLE-9f3a";
        var issue = IngestFixtures.Issue(title: SecretTitle);
        var chunks = IngestFixtures.ChunksOf(issue);
        var blobs = new FakeBlobs(_ => ArtefactJson.Serialize(issue));
        var stub = IngestFixtures.StubFor(chunks.Count, "1017");
        stub.EnqueueJson(IngestFixtures.IndexReply);
        var log = new CapturingLogger();

        await Function(blobs, stub, log).Run(IngestFixtures.EventFor(), Ct);

        var logged = string.Join('\n', log.Entries);
        Assert.NotEmpty(log.Entries);

        // Counts and the artefact's ID are there; the title and the body are not, in any form.
        Assert.Contains(IngestFixtures.ArtefactId, logged);
        Assert.Contains($"{chunks.Count} chunks", logged);
        Assert.DoesNotContain(SecretTitle, logged);
        Assert.DoesNotContain("importer stops after record", logged);
        Assert.All(chunks, chunk => Assert.DoesNotContain(chunk.Content, logged));
    }
}
