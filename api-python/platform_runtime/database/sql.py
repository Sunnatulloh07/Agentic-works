"""Parameterized SQL compilation. Compilation is NOT a working driver or live proof."""
from .contract import name, mutations, selector

DRIVERS = {'sqlite_managed', 'postgres_managed', 'mysql_managed',
           'mariadb_managed', 'sqlserver_managed', 'oracle_managed'}


def compile_sql(raw, tenant, request):
    driver = raw['driver']
    if driver not in DRIVERS:
        raise ValueError('Not an SQL backend')
    params = []
    def quote(value):
        value = name(value)
        if driver in {'mysql_managed', 'mariadb_managed'}:
            return '`' + value + '`'
        if driver == 'sqlserver_managed':
            return '[' + value + ']'
        return '"' + value + '"'
    def bind(value):
        params.append(value)
        if driver in {'postgres_managed', 'mysql_managed', 'mariadb_managed'}:
            return '%s'
        if driver == 'oracle_managed':
            return ':' + str(len(params))
        return '?'
    target = quote(request['resource'])
    if driver != 'sqlite_managed':
        target = quote(raw.get('schema')) + '.' + target
    spec = raw['resources'][request['resource']]
    operation = request['operation']
    if operation == 'insert':
        data = mutations(raw, tenant, request)
        sql = 'INSERT INTO ' + target + ' (' + ','.join(quote(k) for k in data) + ') VALUES ('
        sql += ','.join(bind(v) for v in data.values()) + ')'
    else:
        if operation == 'read':
            prefix = 'SELECT '
            if driver == 'sqlserver_managed':
                prefix += 'TOP (' + bind(request['limit']) + ') '
            sql = prefix + ','.join(quote(k) for k in request['fields']) + ' FROM ' + target
        else:
            data = mutations(raw, tenant, request)
            parts = [quote(k) + ' = ' + bind(v) for k, v in data.items()]
            version = quote(spec['version_field'])
            parts.append(version + ' = ' + version + ' + 1')
            sql = 'UPDATE ' + target + ' SET ' + ','.join(parts)
        filters = selector(raw, tenant, request)
        if filters:
            sql += ' WHERE ' + ' AND '.join(quote(k) + ' = ' + bind(v) for k, v in filters.items())
        if operation == 'read':
            if driver == 'oracle_managed':
                sql += ' FETCH FIRST ' + bind(request['limit']) + ' ROWS ONLY'
            elif driver != 'sqlserver_managed':
                sql += ' LIMIT ' + bind(request['limit'])
    return sql, params
