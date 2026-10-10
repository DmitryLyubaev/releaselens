using Azure.Storage.Blobs;

namespace ReleaseLens.Functions.Ingest;

/// <summary>Reads an artefact's blob. A small seam so tests need no storage account.</summary>
public interface IArtefactBlobs
{
    /// <summary>The blob's text. A blob that does not exist throws (Azure's <c>RequestFailedException</c>, status 404).</summary>
    Task<string> ReadTextAsync(BlobRef blob, CancellationToken cancellationToken);
}

/// <summary>Reads through a <see cref="BlobServiceClient"/>, which holds the app's managed identity.</summary>
public sealed class BlobServiceArtefactBlobs(BlobServiceClient service) : IArtefactBlobs
{
    public async Task<string> ReadTextAsync(BlobRef blob, CancellationToken cancellationToken)
    {
        var client = service.GetBlobContainerClient(blob.Container).GetBlobClient(blob.BlobName);
        var content = await client.DownloadContentAsync(cancellationToken);
        return content.Value.Content.ToString();
    }
}
