using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using ReleaseLens.Functions.Common;

namespace ReleaseLens.Functions.Ingest;

/// <summary>A blob in the storage account the ingest app reads from.</summary>
public sealed record BlobRef(string Container, string BlobName);

/// <summary>
/// Reads the queue message Event Grid writes for a new blob. Whether Event Grid writes plain JSON or
/// base64 of it is not documented, so both are accepted, as is a one-element array (Event Grid's
/// delivery schema is an array). The failures are <see cref="InvalidArtefactException"/>, with
/// messages that say what is wrong and never what was sent: the message is the queue's content, and
/// these reach logs.
/// </summary>
public static class ArtefactEvent
{
    public const string BlobCreated = "Microsoft.Storage.BlobCreated";
    public const string Container = "artefacts-in";

    private const string SubjectPrefix = "/blobServices/default/containers/" + Container + "/blobs/";

    private static readonly UTF8Encoding StrictUtf8 = new(encoderShouldEmitUTF8Identifier: false, throwOnInvalidBytes: true);

    /// <exception cref="InvalidArtefactException">
    /// The message is not one <c>Microsoft.Storage.BlobCreated</c> event for a blob in <c>artefacts-in</c>.
    /// </exception>
    public static BlobRef Parse(string queueMessage)
    {
        ArgumentNullException.ThrowIfNull(queueMessage);

        var text = queueMessage.Trim().TrimStart('﻿');
        if (text.Length == 0)
        {
            throw new InvalidArtefactException("The queue message is empty.");
        }

        // JSON starts with a brace or a bracket and base64 never does.
        if (text[0] is not ('{' or '['))
        {
            text = FromBase64(text);
        }

        JsonNode? parsed;
        try
        {
            parsed = JsonNode.Parse(text);
        }
        catch (JsonException)
        {
            throw new InvalidArtefactException("The queue message is not JSON.");
        }

        var evt = parsed switch
        {
            JsonObject single => single,
            JsonArray { Count: 1 } array => array[0] as JsonObject,
            _ => null,
        } ?? throw new InvalidArtefactException("The queue message is not a single event.");

        if (String(evt["eventType"]) != BlobCreated)
        {
            throw new InvalidArtefactException($"The event is not a {BlobCreated} event.");
        }

        var subject = String(evt["subject"]);
        if (subject is null || !subject.StartsWith(SubjectPrefix, StringComparison.Ordinal)
            || subject.Length == SubjectPrefix.Length)
        {
            throw new InvalidArtefactException($"The event does not name a blob in the {Container} container.");
        }

        return new BlobRef(Container, subject[SubjectPrefix.Length..]);
    }

    private static string? String(JsonNode? node) =>
        node is JsonValue value && value.TryGetValue<string>(out var text) ? text : null;

    private static string FromBase64(string text)
    {
        try
        {
            return StrictUtf8.GetString(Convert.FromBase64String(text)).TrimStart('﻿');
        }
        catch (Exception failure) when (failure is FormatException or ArgumentException)
        {
            // DecoderFallbackException is an ArgumentException.
            throw new InvalidArtefactException("The queue message is neither JSON nor base64 of JSON.");
        }
    }
}
