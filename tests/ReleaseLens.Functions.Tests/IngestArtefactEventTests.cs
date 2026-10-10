using ReleaseLens.Functions.Common;
using ReleaseLens.Functions.Ingest;

namespace ReleaseLens.Functions.Tests;

public class IngestArtefactEventTests
{
    private static readonly BlobRef Expected = new("artefacts-in", "issue-42.json");

    [Fact]
    public void ArtefactEvent_ParsesPlainJson()
    {
        Assert.Equal(Expected, ArtefactEvent.Parse(IngestFixtures.EventFor()));
    }

    [Fact]
    public void ArtefactEvent_ParsesBase64Json()
    {
        Assert.Equal(Expected, ArtefactEvent.Parse(IngestFixtures.Base64(IngestFixtures.EventFor())));
    }

    [Fact]
    public void ArtefactEvent_ParsesAOneEventArray()
    {
        var array = $"[{IngestFixtures.EventFor()}]";

        Assert.Equal(Expected, ArtefactEvent.Parse(array));
        Assert.Equal(Expected, ArtefactEvent.Parse(IngestFixtures.Base64(array)));
    }

    [Fact]
    public void ArtefactEvent_KeepsAFolderInTheBlobName()
    {
        var parsed = ArtefactEvent.Parse(IngestFixtures.EventFor(blobName: "run-7/issue-42.json"));

        Assert.Equal(new BlobRef("artefacts-in", "run-7/issue-42.json"), parsed);
    }

    public static TheoryData<string> Rejected() => new()
    {
        // Another event type.
        IngestFixtures.EventFor(eventType: "Microsoft.Storage.BlobDeleted"),
        // Another container, including one whose name only starts with ours.
        IngestFixtures.EventFor(container: "other"),
        IngestFixtures.EventFor(container: "artefacts-in2"),
        // A subject that names no blob.
        """{"eventType":"Microsoft.Storage.BlobCreated","subject":"/blobServices/default/containers/artefacts-in/blobs/"}""",
        """{"eventType":"Microsoft.Storage.BlobCreated","subject":"/blobServices/default/containers/artefacts-in"}""",
        // Missing or mistyped fields.
        """{"subject":"/blobServices/default/containers/artefacts-in/blobs/a.json"}""",
        """{"eventType":"Microsoft.Storage.BlobCreated"}""",
        """{"eventType":7,"subject":"/blobServices/default/containers/artefacts-in/blobs/a.json"}""",
        // Not one event.
        "[]",
        $"[{IngestFixtures.EventFor()},{IngestFixtures.EventFor()}]",
        "{}",
        "null",
        "42",
        "\"a string\"",
        // Junk, plain and as base64.
        "",
        "   ",
        "this is not json",
        "{\"eventType\":",
        IngestFixtures.Base64("this is not json"),
        IngestFixtures.Base64("{}"),
        "!!!not base64 either!!!",
    };

    [Theory]
    [MemberData(nameof(Rejected))]
    public void ArtefactEvent_RejectsAnotherEventTypeAnotherContainerAndJunk(string message)
    {
        var failure = Assert.Throws<InvalidArtefactException>(() => ArtefactEvent.Parse(message));

        // The message reaches logs: it says what is wrong, not what was sent.
        if (message.Trim().Length > 0)
        {
            Assert.DoesNotContain(message, failure.Message);
        }
    }

    [Fact]
    public void ArtefactEvent_ANullMessage_Throws()
    {
        Assert.Throws<ArgumentNullException>(() => ArtefactEvent.Parse(null!));
    }
}
