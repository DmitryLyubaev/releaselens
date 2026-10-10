namespace ReleaseLens.Functions.Common;

/// <summary>One document to write to the index: a chunk's key, its artefact, its text and its embedding.</summary>
public sealed record IndexChunk(string ChunkId, string Artefact, string Content, float[] Vector);

/// <summary>One result of a hybrid semantic search; <paramref name="Score"/> is the semantic reranker's score.</summary>
public sealed record SearchHit(string ChunkId, string Artefact, string Content, double Score);
