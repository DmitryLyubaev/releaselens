using ReleaseLens.Core;
using Xunit;

namespace ReleaseLens.Core.Tests;

public class ScaffoldTests
{
    [Fact]
    public void AssemblyMarker_ExposesProjectName()
    {
        Assert.Equal("ReleaseLens", CoreAssembly.ProjectName);
    }
}
