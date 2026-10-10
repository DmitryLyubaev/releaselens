using System.Diagnostics;
using Microsoft.Azure.Functions.Worker;
using Microsoft.Extensions.Logging;
using ReleaseLens.Functions.Common;

namespace ReleaseLens.Functions.Ingest;

/// <summary>
/// The queue trigger: the event names a blob, the blob is an artefact, and <see cref="IngestArtefact"/>
/// makes it searchable. A failure anywhere throws: the queue tries three times, then moves the
/// message to <c>ingest-events-poison</c>. It logs counts, durations and the artefact's ID, never the
/// artefact's text.
/// </summary>
public sealed class IngestFunction(IArtefactBlobs blobs, IngestArtefact ingest, ILogger<IngestFunction> log)
{
    [Function("ingest")]
    public async Task Run(
        [QueueTrigger("ingest-events", Connection = "IngestQueue")] string message,
        CancellationToken cancellationToken)
    {
        var started = Stopwatch.GetTimestamp();

        var blob = ArtefactEvent.Parse(message);
        var record = ArtefactJson.Parse(await blobs.ReadTextAsync(blob, cancellationToken));
        var result = await ingest.IngestAsync(record, cancellationToken);

        log.LogInformation(
            "Ingested {Artefact}: {Chunks} chunks, {Deleted} stale keys removed, in {ElapsedMs} ms.",
            result.Artefact,
            result.Chunks,
            result.Deleted,
            (long)Stopwatch.GetElapsedTime(started).TotalMilliseconds);
    }
}
