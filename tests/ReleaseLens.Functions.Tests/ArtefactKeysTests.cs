using System.Text;
using ReleaseLens.Core.Evidence;
using ReleaseLens.Functions.Common;

namespace ReleaseLens.Functions.Tests;

public class ArtefactKeysTests
{
    [Theory]
    [InlineData("issue:123", EntityType.Issue, "123")]
    [InlineData("pull_request:45", EntityType.PullRequest, "45")]
    [InlineData("commit:0123456789abcdef0123456789abcdef01234567", EntityType.Commit, "0123456789abcdef0123456789abcdef01234567")]
    [InlineData("release:python-1.44.1", EntityType.Release, "python-1.44.1")]
    [InlineData("release:v1:rc1", EntityType.Release, "v1:rc1")]
    public void ArtefactKeys_ParsesEachType(string text, EntityType type, string value)
    {
        var key = ArtefactKeys.Parse(text);

        Assert.Equal(new EvidenceKey(type, value), key);
        Assert.Equal(text, key.ToString());
    }

    [Theory]
    [InlineData("")]
    [InlineData("   ")]
    [InlineData("issue")]
    [InlineData("issue:")]
    [InlineData(":123")]
    [InlineData("widget:1")]
    [InlineData("Issue:1")]
    [InlineData("issue:abc")]
    [InlineData("issue:0")]
    [InlineData("issue:-4")]
    [InlineData("issue:007")]
    [InlineData("issue:+7")]
    [InlineData("issue: 7")]
    [InlineData("pull_request:1.5")]
    [InlineData("issue:99999999999")]
    [InlineData("commit:")]
    [InlineData("release:")]
    public void ArtefactKeys_RejectsJunk(string text)
    {
        var failure = Assert.Throws<FormatException>(() => ArtefactKeys.Parse(text));

        Assert.Contains($"'{text}'", failure.Message);
    }

    [Fact]
    public void ArtefactKeys_RejectsNull()
    {
        Assert.Throws<ArgumentNullException>(() => ArtefactKeys.Parse(null!));
    }

    [Fact]
    public void ArtefactKeys_FileNameIsTheKeysBase64UrlWithoutPaddingThenJson()
    {
        var key = new EvidenceKey(EntityType.Release, "v1.0+build/7?");

        var name = ArtefactKeys.FileName(key);

        var expected = Convert.ToBase64String(Encoding.UTF8.GetBytes("release:v1.0+build/7?"))
            .TrimEnd('=').Replace('+', '-').Replace('/', '_');
        Assert.Equal($"{expected}.json", name);
        Assert.Matches(@"^[A-Za-z0-9_-]+\.json$", name);
    }

    [Fact]
    public void ArtefactKeys_FileNameForAnIssue()
    {
        Assert.Equal("aXNzdWU6MTIz.json", ArtefactKeys.FileName(new EvidenceKey(EntityType.Issue, "123")));
    }
}
