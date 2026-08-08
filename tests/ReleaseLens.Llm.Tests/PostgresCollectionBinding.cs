using ReleaseLens.Storage.Tests;
using Xunit;

namespace ReleaseLens.Llm.Tests;

/// <summary>
/// xUnit resolves a collection's <c>[CollectionDefinition]</c> only within the test
/// assembly that references it by name - it does not scan referenced assemblies for one.
/// <see cref="PostgresCollection"/> lives in ReleaseLens.Storage.Tests, so this assembly
/// needs its own binding under the same collection name to get <see cref="PostgresFixture"/>
/// constructor-injected, without duplicating the fixture (the container bootstrap) itself.
/// </summary>
[CollectionDefinition(nameof(PostgresCollection))]
public sealed class PostgresCollectionBinding : ICollectionFixture<PostgresFixture>;
