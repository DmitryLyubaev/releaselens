using System;
using System.Threading.Tasks;
using ReleaseLens.Storage.Retrieval;
using Xunit;

namespace ReleaseLens.Storage.Tests;

/// <summary>
/// The arguments <see cref="ExactVectorSearch.SearchAsync"/> refuses. They are checked before
/// the scope is touched, so these need no database: the scope passed is null.
/// </summary>
public class ExactVectorSearchArgumentTests
{
    [Theory]
    [InlineData(0)]
    [InlineData(-1)]
    public async Task SearchAsync_RefusesAKThatIsNotPositive(int k)
    {
        var error = await Assert.ThrowsAsync<ArgumentOutOfRangeException>(() => new ExactVectorSearch().SearchAsync(
            null!, new float[ExactVectorSearch.Dimensions], k, TestContext.Current.CancellationToken));

        Assert.Equal("k", error.ParamName);
    }

    [Theory]
    [InlineData(0)]
    [InlineData(383)]
    [InlineData(1536)]
    public async Task SearchAsync_RefusesAQueryVectorThatIsNot384Dimensions(int length)
    {
        var error = await Assert.ThrowsAsync<ArgumentOutOfRangeException>(() => new ExactVectorSearch().SearchAsync(
            null!, new float[length], 50, TestContext.Current.CancellationToken));

        Assert.Equal("queryVector", error.ParamName);
    }
}
