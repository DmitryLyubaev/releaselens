using System.Net;
using System.Text;
using Azure;
using Azure.Core.Pipeline;
using Azure.Storage.Blobs;
using ReleaseLens.Functions.Ingest;

namespace ReleaseLens.Functions.Tests;

/// <summary>The real blob reader, over the Azure SDK's own pipeline with a stubbed transport: no network.</summary>
public class IngestBlobTests
{
    private sealed class BlobHandler(Func<HttpRequestMessage, HttpResponseMessage> reply) : HttpMessageHandler
    {
        public List<Uri> Requests { get; } = [];

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            Requests.Add(request.RequestUri!);
            return Task.FromResult(reply(request));
        }
    }

    private static BlobServiceArtefactBlobs Blobs(BlobHandler handler) =>
        new(new BlobServiceClient(
            new Uri("https://example.blob.core.windows.net"),
            new BlobClientOptions { Transport = new HttpClientTransport(new HttpClient(handler)) }));

    [Fact]
    public async Task Blob_ReadsTheNamedBlobAsText()
    {
        var handler = new BlobHandler(_ =>
        {
            var response = new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent("""{"entityType":"issue"}""", Encoding.UTF8, "application/json"),
            };
            response.Headers.ETag = new System.Net.Http.Headers.EntityTagHeaderValue("\"0x8DC\"");
            response.Content.Headers.LastModified = new DateTimeOffset(2026, 10, 10, 9, 30, 0, TimeSpan.Zero);
            response.Headers.Add("x-ms-blob-type", "BlockBlob");
            return response;
        });

        var text = await Blobs(handler).ReadTextAsync(
            new BlobRef("artefacts-in", "run-7/issue-42.json"), TestContext.Current.CancellationToken);

        Assert.Equal("""{"entityType":"issue"}""", text);
        Assert.Equal("/artefacts-in/run-7/issue-42.json", Assert.Single(handler.Requests).AbsolutePath);
    }

    [Fact]
    public async Task Blob_ABlobThatIsGone_Throws()
    {
        var handler = new BlobHandler(_ =>
        {
            var response = new HttpResponseMessage(HttpStatusCode.NotFound);
            response.Headers.Add("x-ms-error-code", "BlobNotFound");
            return response;
        });

        var failure = await Assert.ThrowsAsync<RequestFailedException>(
            () => Blobs(handler).ReadTextAsync(new BlobRef("artefacts-in", "gone.json"), TestContext.Current.CancellationToken));

        Assert.Equal(404, failure.Status);
    }
}
