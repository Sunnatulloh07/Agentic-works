"""Tenant/agent-scoped document chunks, BM25 and optional pinned-model vectors.

Text ingestion is local. Binary extraction, URL crawling, provider embeddings and
semantic quality acceptance are separate capabilities, not simulated here.
"""
import collections
import math
import re
import unicodedata
from urllib.parse import urlsplit

from .engine import Conflict, Forbidden, NotFound, digest, encode

SCHEMA = '''
CREATE TABLE IF NOT EXISTS p_kb_collections(
 tenant TEXT NOT NULL,id TEXT NOT NULL,model TEXT NOT NULL DEFAULT '',dimension INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(tenant,id));
CREATE TABLE IF NOT EXISTS p_kb_acl(
 tenant TEXT NOT NULL,collection TEXT NOT NULL,agent TEXT NOT NULL,
 PRIMARY KEY(tenant,collection,agent),FOREIGN KEY(tenant,collection) REFERENCES p_kb_collections(tenant,id));
CREATE TABLE IF NOT EXISTS p_kb_documents(
 tenant TEXT NOT NULL,collection TEXT NOT NULL,id TEXT NOT NULL,version INTEGER NOT NULL,
 title TEXT NOT NULL,source_url TEXT NOT NULL,content_hash TEXT NOT NULL,updated REAL NOT NULL,deleted INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(tenant,collection,id),FOREIGN KEY(tenant,collection) REFERENCES p_kb_collections(tenant,id));
CREATE TABLE IF NOT EXISTS p_kb_chunks(
 tenant TEXT NOT NULL,collection TEXT NOT NULL,document TEXT NOT NULL,id TEXT NOT NULL,
 position INTEGER NOT NULL,start INTEGER NOT NULL,end INTEGER NOT NULL,text TEXT NOT NULL,vector TEXT NOT NULL DEFAULT '[]',
 PRIMARY KEY(tenant,collection,id),
 FOREIGN KEY(tenant,collection,document) REFERENCES p_kb_documents(tenant,collection,id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS p_kb_chunk_scope ON p_kb_chunks(tenant,collection,document,position);
'''
MAX_DOCUMENT_CHARS = 100000
MAX_COLLECTION_CHUNKS = 4000
CHUNK_SIZE = 512
OVERLAP = 64


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', value):
        raise ValueError('Bounded knowledge identifier required')
    return value


def chunks(text):
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_DOCUMENT_CHARS or '\x00' in text:
        raise ValueError('Knowledge text must be nonempty, bounded and contain no NUL')
    # Keep original text and exact character offsets for quotations.
    out = []
    start = 0
    while start < len(text):
        end = min(len(text), start + CHUNK_SIZE)
        out.append((start, end, text[start:end]))
        if end == len(text): break
        start = end - OVERLAP
    return out


def tokens(text):
    text = unicodedata.normalize('NFKC', text).lower()
    for mark in ['‘', '’', 'ʻ', 'ʼ', '`']:
        text = text.replace(mark, "'")
    return re.findall(r"[^\W_]+(?:'[^\W_]+)*", text, flags=re.UNICODE)


def vector(value, dimension):
    if not isinstance(value, list) or len(value) != dimension:
        raise ValueError('Embedding dimension mismatch')
    out = []
    for n in value:
        # The ORDER is load-bearing. `math.isfinite` converts to float, and
        # `math.isfinite(10 ** 400)` RAISES OverflowError, so a finiteness check
        # placed first lets an integer beyond the float range escape the bound that
        # exists to refuse it -- the guard dies instead of the value. `abs()` needs
        # no conversion, so the magnitude check goes first and refuses it; the
        # finiteness check then still catches nan, for which every comparison is
        # False. Measured: `vector([10**400, 1], 2)` used to raise OverflowError.
        if type(n) not in (int, float) or abs(n) > 1e6 or not math.isfinite(n):
            raise ValueError('Embedding must contain bounded finite numbers')
        out.append(float(n))
    norm = math.sqrt(sum(n * n for n in out))
    if norm == 0: raise ValueError('Zero embedding denied')
    return [n / norm for n in out]


def bm25(query, texts):
    docs = [collections.Counter(tokens(text)) for text in texts]
    lengths = [sum(doc.values()) for doc in docs]
    average = sum(lengths) / len(docs) if docs else 1
    terms = set(tokens(query))
    frequencies = {term: sum(term in doc for doc in docs) for term in terms}
    scores = []
    for doc, length in zip(docs, lengths):
        score = 0.0
        for term in terms:
            tf = doc[term]
            if not tf: continue
            idf = math.log(1 + (len(docs) - frequencies[term] + 0.5) / (frequencies[term] + 0.5))
            score += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * length / (average or 1)))
        scores.append(score)
    return scores


class KnowledgeStore:
    def __init__(self, engine): self.engine = engine

    def create_collection(self, tenant, actor, collection, model='', dimension=0):
        identifier(collection)
        if type(dimension) is not int or not 0 <= dimension <= 1024 or not isinstance(model, str) or len(model) > 256:
            raise ValueError('Invalid embedding model configuration')
        if bool(model.strip()) != bool(dimension):
            raise ValueError('Embedding model and dimension must be configured together')
        with self.engine.tx() as db:
            self.engine.require_authority(db, tenant, 'web', actor, ('owner',))
            self.engine.require_active(db, tenant)
            old = db.execute('SELECT * FROM p_kb_collections WHERE tenant=? AND id=?', (tenant, collection)).fetchone()
            if old:
                if (old['model'], old['dimension']) != (model, dimension):
                    raise Conflict('Collection embedding identity is immutable; create a new collection')
                return {'id': collection, 'created': False}
            db.execute('INSERT INTO p_kb_collections VALUES(?,?,?,?)', (tenant, collection, model, dimension))
            self.engine.audit(db, tenant, '', 'knowledge.collection_created', actor, {'collection': collection, 'model': model, 'dimension': dimension})
        return {'id': collection, 'created': True}

    def _collection(self, db, tenant, collection):
        row = db.execute('SELECT * FROM p_kb_collections WHERE tenant=? AND id=?', (tenant, collection)).fetchone()
        if not row: raise NotFound('Knowledge collection not found')
        return row

    def grant(self, tenant, actor, collection, agent, allowed=True):
        identifier(collection)
        if not isinstance(agent, str) or not agent.strip() or len(agent) > 128 or type(allowed) is not bool:
            raise ValueError('Explicit agent and boolean grant required')
        self.engine.policy(tenant, agent)  # Pack validation for deployed engines.
        with self.engine.tx() as db:
            self.engine.require_authority(db, tenant, 'web', actor, ('owner',))
            self.engine.require_active(db, tenant)
            self._collection(db, tenant, collection)
            if allowed:
                db.execute('INSERT OR IGNORE INTO p_kb_acl VALUES(?,?,?)', (tenant, collection, agent))
            else:
                db.execute('DELETE FROM p_kb_acl WHERE tenant=? AND collection=? AND agent=?', (tenant, collection, agent))
            self.engine.audit(db, tenant, '', 'knowledge.grant_changed', actor, {'collection': collection, 'agent': agent, 'allowed': allowed})

    def ingest(self, tenant, actor, collection, document, title, content, expected_version=0, source_url='', *, vectors=None, model=''):
        identifier(collection); identifier(document)
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise ValueError('Bounded document title required')
        if not isinstance(source_url, str) or len(source_url) > 2000 or any(ord(c) < 32 for c in source_url):
            raise ValueError('Invalid document source reference')
        if source_url:
            url = urlsplit(source_url)
            if url.scheme not in {'https', 'http'} or not url.hostname or url.username or url.password:
                raise ValueError('Only credential-free HTTP(S) source references allowed')
        if type(expected_version) is not int or not 0 <= expected_version < 2**31:
            raise ValueError('Explicit expected document version required')
        parts = chunks(content)
        with self.engine.tx() as db:
            self.engine.require_authority(db, tenant, 'web', actor, ('owner', 'operator', 'integrator'))
            self.engine.require_active(db, tenant)
            cfg = self._collection(db, tenant, collection)
            if cfg['dimension']:
                if model != cfg['model'] or not isinstance(vectors, list) or len(vectors) != len(parts):
                    raise ValueError('Pinned-model embedding required for every document chunk')
                embeddings = [vector(v, cfg['dimension']) for v in vectors]
            else:
                if vectors is not None or model:
                    raise ValueError('Lexical collection does not accept embeddings')
                embeddings = [[] for _ in parts]
            old = db.execute('SELECT * FROM p_kb_documents WHERE tenant=? AND collection=? AND id=?', (tenant, collection, document)).fetchone()
            actual = old['version'] if old else 0
            if actual != expected_version: raise Conflict('Document version changed')
            count = db.execute('SELECT count(*) FROM p_kb_chunks WHERE tenant=? AND collection=? AND document<>?', (tenant, collection, document)).fetchone()[0]
            if count + len(parts) > MAX_COLLECTION_CHUNKS:
                raise ValueError('Knowledge collection chunk limit reached')
            version = actual + 1
            db.execute('INSERT INTO p_kb_documents VALUES(?,?,?,?,?,?,?,?,0) ON CONFLICT(tenant,collection,id) DO UPDATE SET '
                'version=excluded.version,title=excluded.title,source_url=excluded.source_url,content_hash=excluded.content_hash,updated=excluded.updated,deleted=0',
                (tenant, collection, document, version, title, source_url, digest(content), self.engine.clock()))
            db.execute('DELETE FROM p_kb_chunks WHERE tenant=? AND collection=? AND document=?', (tenant, collection, document))
            ids = []
            for pos, ((start, end, text), embedding) in enumerate(zip(parts, embeddings)):
                chunk_id = digest({'tenant': tenant, 'collection': collection, 'document': document, 'version': version, 'position': pos, 'text': text})
                db.execute('INSERT INTO p_kb_chunks VALUES(?,?,?,?,?,?,?,?,?)', (tenant, collection, document, chunk_id, pos, start, end, text, encode(embedding)))
                ids.append('kb:' + chunk_id)
            self.engine.audit(db, tenant, '', 'knowledge.document_ingested', actor, {'collection': collection, 'document': document, 'version': version, 'chunks': len(parts)})
        return {'document': document, 'version': version, 'chunks': len(parts), 'source_ids': ids}

    def delete(self, tenant, actor, collection, document, expected_version):
        if type(expected_version) is not int or expected_version < 1: raise ValueError('Expected document version required')
        with self.engine.tx() as db:
            self.engine.require_authority(db, tenant, 'web', actor, ('owner',))
            self.engine.require_active(db, tenant)
            result = db.execute('UPDATE p_kb_documents SET deleted=1,version=version+1,updated=? WHERE tenant=? AND collection=? AND id=? AND version=? AND deleted=0', (self.engine.clock(), tenant, collection, document, expected_version))
            if result.rowcount != 1: raise Conflict('Document missing or version changed')
            db.execute('DELETE FROM p_kb_chunks WHERE tenant=? AND collection=? AND document=?', (tenant, collection, document))
            self.engine.audit(db, tenant, '', 'knowledge.document_deleted', actor, {'collection': collection, 'document': document, 'version': expected_version})

    def documents(self, tenant, collection, limit=100):
        identifier(collection)
        if type(limit) is not int or not 1 <= limit <= 100: raise ValueError('Invalid list limit')
        with self.engine.read() as db:
            self._collection(db, tenant, collection)
            return [dict(row) for row in db.execute('SELECT id,version,title,source_url,updated,deleted FROM p_kb_documents '
                'WHERE tenant=? AND collection=? ORDER BY id LIMIT ?', (tenant, collection, limit))]

    def search(self, tenant, agent, collection, query, limit=4, *, query_vector=None, model=''):
        import json
        identifier(collection)
        if not isinstance(query, str) or not query.strip() or len(query) > 500 or not tokens(query):
            raise ValueError('Bounded nonempty knowledge query required')
        if type(limit) is not int or not 1 <= limit <= 5: raise ValueError('Knowledge result limit must be 1..5')
        if 'knowledge.search' not in self.engine.policy(tenant, agent).get('tools', []):
            raise Forbidden('Agent knowledge tool permission required')
        with self.engine.read() as db:
            # One snapshot covers ACL, configuration and data to avoid partial reads.
            db.execute('BEGIN')
            self.engine.require_active(db, tenant)
            grant = db.execute('SELECT 1 FROM p_kb_acl WHERE tenant=? AND collection=? AND agent=?', (tenant, collection, agent)).fetchone()
            if not grant: raise Forbidden('Agent knowledge collection access denied')
            cfg = self._collection(db, tenant, collection)
            if query_vector is not None:
                if not cfg['dimension'] or model != cfg['model']: raise ValueError('Query embedding model mismatch')
                query_vector = vector(query_vector, cfg['dimension'])
            elif model:
                raise ValueError('Model without query vector denied')
            rows = [dict(row) for row in db.execute('SELECT c.*,d.version,d.title,d.source_url FROM p_kb_chunks c JOIN p_kb_documents d '
                'ON d.tenant=c.tenant AND d.collection=c.collection AND d.id=c.document '
                'WHERE c.tenant=? AND c.collection=? AND d.deleted=0 ORDER BY c.document,c.position LIMIT ?', (tenant, collection, MAX_COLLECTION_CHUNKS+1))]
            if len(rows) > MAX_COLLECTION_CHUNKS: raise ValueError('Knowledge corpus exceeds local search limit')
        lexical = bm25(query, [row['text'] for row in rows])
        rank = {i: score for i, score in enumerate(lexical) if score > 0}
        method = 'bm25'
        if query_vector is not None:
            similarities = [sum(a*b for a,b in zip(query_vector, vector(json.loads(row['vector']), cfg['dimension']))) for row in rows]
            # Reciprocal-rank fusion avoids mixing raw BM25 and cosine scales.
            rank = {}
            for scores in (lexical, similarities):
                selected = sorted((i for i, s in enumerate(scores) if s > 0), key=lambda i: (-scores[i], rows[i]['id']))
                for position, i in enumerate(selected, 1): rank[i] = rank.get(i, 0) + 1 / (60 + position)
            method = 'hybrid_bm25_cosine_rrf'
        ordered = sorted(rank, key=lambda i: (-rank[i], rows[i]['id']))
        selected = ordered[:limit]
        # The rank is total, so comparing lengths answers "was there more" without
        # the len(...) >= limit trap: a corpus of exactly `limit` matching chunks
        # returns `limit` and is NOT truncated.
        matches_truncated = len(ordered) > len(selected)
        results = []
        for i in selected:
            row = rows[i]
            results.append({'source_id': 'kb:' + row['id'], 'document': row['document'], 'version': row['version'],
                'title': row['title'], 'source_url': row['source_url'], 'start': row['start'], 'end': row['end'],
                'text': row['text'], 'untrusted_content': True})
        return {'collection': collection, 'matches': results, 'retrieval': method,
                'semantic_fact_check': 'not_performed',
                # How many chunks matched, against how many were returned. Without
                # both, five extracted passages out of fifty are indistinguishable
                # from five out of five, and the agent reasons from a tenth of the
                # corpus believing it is the whole of it.
                'matched': len(ordered), 'returned': len(selected),
                'truncated': matches_truncated}


def tool_search(engine, tenant, agent, args, step):
    return KnowledgeStore(engine).search(tenant, agent, args['collection'], args['query'], args.get('limit', 4))
