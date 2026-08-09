using System.Data;
using Dapper;

namespace ReleaseLens.Storage;

/// <summary>
/// Dapper's parameter binding looks up a DbType for the CLR type of every parameter value
/// before an NpgsqlParameter is ever created, and its static type map predates
/// <see cref="DateOnly"/> - passing one as a bare Dapper parameter throws
/// NotSupportedException at the first call, not at compile time. Registered once,
/// process-wide, in <see cref="TenantConnectionFactory"/>'s static constructor alongside
/// the vector handler, since that is the one place every Dapper call passes through.
/// </summary>
public sealed class DateOnlyTypeHandler : SqlMapper.TypeHandler<DateOnly>
{
    public override DateOnly Parse(object value) => value switch
    {
        DateOnly dateOnly => dateOnly,
        DateTime dateTime => DateOnly.FromDateTime(dateTime),
        _ => throw new NotSupportedException($"Cannot convert {value.GetType()} to DateOnly.")
    };

    public override void SetValue(IDbDataParameter parameter, DateOnly value) => parameter.Value = value;
}
