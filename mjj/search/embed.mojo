"""Native search kernels: int8 top-k scan, identifier tokenize, static embed, BM25, quantize."""

from std.algorithm import parallelize
from std.math import abs, copysign, sqrt
from std.memory import alloc
from std.sys.info import num_performance_cores, simd_width_of

comptime F32Ptr = UnsafePointer[Float32, AnyOrigin[mut=True]]
comptime F64Ptr = UnsafePointer[Float64, AnyOrigin[mut=True]]
comptime I8Ptr = UnsafePointer[Int8, AnyOrigin[mut=True]]
comptime I32Ptr = UnsafePointer[Int32, AnyOrigin[mut=True]]
comptime I64Ptr = UnsafePointer[Int64, AnyOrigin[mut=True]]
comptime U8Ptr = UnsafePointer[UInt8, AnyOrigin[mut=True]]
comptime I8W = simd_width_of[DType.int8]()
comptime F64W = simd_width_of[DType.float64]()
comptime PARALLEL_WORK_THRESHOLD = 100_000_000
comptime TOKEN_PREFIX_LOW = UInt32(2774198458)
comptime TOKEN_PREFIX_HIGH = UInt32(4130110811)
comptime NGRAM_PREFIX_LOW = UInt32(911368588)
comptime NGRAM_PREFIX_HIGH = UInt32(1696920685)
comptime HASH_SIGN_BIT = UInt64(1) << 63
comptime START_MARKER = UInt8(ord("^"))
comptime END_MARKER = UInt8(ord("$"))


def f32p(addr: Int) -> F32Ptr:
    return F32Ptr(unsafe_from_address=addr)


def f64p(addr: Int) -> F64Ptr:
    return F64Ptr(unsafe_from_address=addr)


def i8p(addr: Int) -> I8Ptr:
    return I8Ptr(unsafe_from_address=addr)


def i32p(addr: Int) -> I32Ptr:
    return I32Ptr(unsafe_from_address=addr)


def i64p(addr: Int) -> I64Ptr:
    return I64Ptr(unsafe_from_address=addr)


def u8p(addr: Int) -> U8Ptr:
    return U8Ptr(unsafe_from_address=addr)


def dot_i8_ptr(a: I8Ptr, b: I8Ptr, n: Int) -> Int32:
    var acc0 = SIMD[DType.int32, I8W](0)
    var acc1 = SIMD[DType.int32, I8W](0)
    var i = 0
    while i + 2 * I8W <= n:
        var av0 = a.load[width=I8W](i).cast[DType.int32]()
        var bv0 = b.load[width=I8W](i).cast[DType.int32]()
        var av1 = a.load[width=I8W](i + I8W).cast[DType.int32]()
        var bv1 = b.load[width=I8W](i + I8W).cast[DType.int32]()
        acc0 += av0 * bv0
        acc1 += av1 * bv1
        i += 2 * I8W
    while i + I8W <= n:
        var av = a.load[width=I8W](i).cast[DType.int32]()
        var bv = b.load[width=I8W](i).cast[DType.int32]()
        acc0 += av * bv
        i += I8W
    var total = (acc0 + acc1).reduce_add()
    while i < n:
        total += Int32(Int(a[i])) * Int32(Int(b[i]))
        i += 1
    return total


def score_rows(
    data: I8Ptr,
    factors: F32Ptr,
    query: I8Ptr,
    query_factor: Float32,
    scores: F32Ptr,
    count: Int,
    dim: Int,
):
    var workers = 1
    if count * dim >= PARALLEL_WORK_THRESHOLD:
        workers = min(num_performance_cores(), max(1, count // 8_000))

    @parameter
    def scan(worker: Int):
        var start = worker * count // workers
        var end = (worker + 1) * count // workers
        for row in range(start, end):
            scores[row] = (
                Float32(Int(dot_i8_ptr(query, data + row * dim, dim)))
                * query_factor
                * factors[row]
            )

    if count * dim >= PARALLEL_WORK_THRESHOLD and workers > 1:
        parallelize[scan](workers, workers)
    else:
        scan(0)


@export("mjj_search_i8_mmap")
def mjj_search_i8_mmap(
    data_addr: Int,
    factors_addr: Int,
    ids_addr: Int,
    query_addr: Int,
    scores_addr: Int,
    output_ids_addr: Int,
    output_scores_addr: Int,
    count: Int,
    dim: Int,
    k: Int,
    query_factor: Float32,
) abi("C"):
    if (
        count <= 0
        or dim <= 0
        or k <= 0
        or data_addr == 0
        or factors_addr == 0
        or ids_addr == 0
        or query_addr == 0
        or scores_addr == 0
        or output_ids_addr == 0
        or output_scores_addr == 0
    ):
        return
    var data = i8p(data_addr)
    var factors = f32p(factors_addr)
    var ids = i64p(ids_addr)
    var query = i8p(query_addr)
    var scores = f32p(scores_addr)
    var output_ids = i64p(output_ids_addr)
    var output_scores = f32p(output_scores_addr)
    var wanted = min(k, count)
    score_rows(data, factors, query, query_factor, scores, count, dim)

    for slot in range(wanted):
        output_ids[slot] = -1
        output_scores[slot] = -1.0e30
    for row in range(count):
        var value = scores[row]
        if value <= output_scores[wanted - 1]:
            continue
        var slot = wanted - 1
        while slot > 0 and value > output_scores[slot - 1]:
            output_scores[slot] = output_scores[slot - 1]
            output_ids[slot] = output_ids[slot - 1]
            slot -= 1
        output_scores[slot] = value
        output_ids[slot] = ids[row]


def crc32_table() -> InlineArray[UInt32, 256]:
    var table = InlineArray[UInt32, 256](fill=0)
    var index = 0
    while index < 256:
        var crc = UInt32(index)
        var bit = 0
        while bit < 8:
            if (crc & 1) != 0:
                crc = (crc >> 1) ^ 0xEDB88320
            else:
                crc = crc >> 1
            bit += 1
        table[index] = crc
        index += 1
    return table


comptime CRC32_TABLE = crc32_table()


def crc32_update(data: U8Ptr, length: Int, value: UInt32) -> UInt32:
    var crc = value ^ 0xFFFFFFFF
    var i = 0
    while i < length:
        var index = Int((crc ^ UInt32(Int(data[i]))) & 0xFF)
        crc = CRC32_TABLE[index] ^ (crc >> 8)
        i += 1
    return crc ^ 0xFFFFFFFF


def is_ascii_alpha(byte: UInt8) -> Bool:
    return (byte >= UInt8(ord("A")) and byte <= UInt8(ord("Z"))) or (
        byte >= UInt8(ord("a")) and byte <= UInt8(ord("z"))
    )


def is_ascii_digit(byte: UInt8) -> Bool:
    return byte >= UInt8(ord("0")) and byte <= UInt8(ord("9"))


def is_ascii_alnum(byte: UInt8) -> Bool:
    return is_ascii_alpha(byte) or is_ascii_digit(byte)


def is_ascii_upper(byte: UInt8) -> Bool:
    return byte >= UInt8(ord("A")) and byte <= UInt8(ord("Z"))


def is_ascii_lower(byte: UInt8) -> Bool:
    return byte >= UInt8(ord("a")) and byte <= UInt8(ord("z"))


def to_ascii_lower(byte: UInt8) -> UInt8:
    if is_ascii_upper(byte):
        return byte + UInt8(32)
    return byte


def camel_boundary_before(word: U8Ptr, length: Int, index: Int) -> Bool:
    if index <= 0 or index >= length:
        return False
    var prev = word[index - 1]
    var curr = word[index]
    if (is_ascii_lower(prev) or is_ascii_digit(prev)) and is_ascii_upper(curr):
        return True
    if (
        is_ascii_upper(prev)
        and is_ascii_upper(curr)
        and index + 1 < length
        and is_ascii_lower(word[index + 1])
    ):
        return True
    return False


def word_all_lower_or_digit(word: U8Ptr, length: Int) -> Bool:
    var all_digit = True
    var all_lower = True
    var i = 0
    while i < length:
        var byte = word[i]
        if not is_ascii_digit(byte):
            all_digit = False
        if not is_ascii_lower(byte):
            all_lower = False
        i += 1
    return all_digit or all_lower


def emit_token(
    source: U8Ptr,
    start: Int,
    length: Int,
    dest: U8Ptr,
    out_cap: Int,
    out_len: Int,
    offsets: I32Ptr,
    lengths: I32Ptr,
    max_tokens: Int,
    token_count: Int,
) -> Tuple[Int, Int, Bool]:
    """Copy one lowercased token. Returns (token_count, out_len, ok)."""
    if token_count >= max_tokens or out_len + length > out_cap:
        return (token_count, out_len, False)
    offsets[token_count] = Int32(out_len)
    lengths[token_count] = Int32(length)
    var i = 0
    while i < length:
        dest[out_len + i] = to_ascii_lower(source[start + i])
        i += 1
    return (token_count + 1, out_len + length, True)


def emit_word_tokens(
    text: U8Ptr,
    start: Int,
    length: Int,
    dest: U8Ptr,
    out_cap: Int,
    out_len: Int,
    offsets: I32Ptr,
    lengths: I32Ptr,
    max_tokens: Int,
    token_count: Int,
) -> Tuple[Int, Int, Bool]:
    var word = text + start
    var count = token_count
    var written = out_len
    var ok = True
    count, written, ok = emit_token(
        word, 0, length, dest, out_cap, written, offsets, lengths, max_tokens, count
    )
    if not ok:
        return (count, written, False)
    if word_all_lower_or_digit(word, length):
        return (count, written, True)

    var piece_start = 0
    var index = 1
    while index < length:
        if camel_boundary_before(word, length, index):
            var piece_len = index - piece_start
            if piece_len > 0 and piece_len != length:
                count, written, ok = emit_token(
                    word,
                    piece_start,
                    piece_len,
                    dest,
                    out_cap,
                    written,
                    offsets,
                    lengths,
                    max_tokens,
                    count,
                )
                if not ok:
                    return (count, written, False)
            piece_start = index
        index += 1
    var tail_len = length - piece_start
    if piece_start > 0 and tail_len > 0 and tail_len != length:
        count, written, ok = emit_token(
            word,
            piece_start,
            tail_len,
            dest,
            out_cap,
            written,
            offsets,
            lengths,
            max_tokens,
            count,
        )
        if not ok:
            return (count, written, False)
    return (count, written, True)


@export("mjj_tokenize")
def mjj_tokenize(
    text_addr: Int,
    text_len: Int,
    out_addr: Int,
    out_cap: Int,
    offsets_addr: Int,
    lengths_addr: Int,
    max_tokens: Int,
    token_count_out_addr: Int,
    out_len_out_addr: Int,
) abi("C") -> Int:
    """Tokenise ASCII identifiers. Returns 0 on success, 1 if buffers are short.

    On success, *token_count_out / *out_len_out hold written sizes. On overflow
    they hold a lower bound of what was consumed before capacity ran out; the
    Python wrapper retries with a larger buffer.
    """
    if (
        text_addr == 0
        or out_addr == 0
        or offsets_addr == 0
        or lengths_addr == 0
        or token_count_out_addr == 0
        or out_len_out_addr == 0
        or text_len < 0
        or out_cap < 0
        or max_tokens < 0
    ):
        return 1
    var text = u8p(text_addr)
    var dest = u8p(out_addr)
    var offsets = i32p(offsets_addr)
    var lengths = i32p(lengths_addr)
    var token_count_out = i64p(token_count_out_addr)
    var out_len_out = i64p(out_len_out_addr)
    var token_count = 0
    var out_len = 0
    var ok = True
    var index = 0
    while index < text_len:
        var byte = text[index]
        if is_ascii_alpha(byte):
            var start = index
            index += 1
            while index < text_len and is_ascii_alnum(text[index]):
                index += 1
            token_count, out_len, ok = emit_word_tokens(
                text,
                start,
                index - start,
                dest,
                out_cap,
                out_len,
                offsets,
                lengths,
                max_tokens,
                token_count,
            )
            if not ok:
                token_count_out[0] = Int64(token_count)
                out_len_out[0] = Int64(out_len)
                return 1
        elif is_ascii_digit(byte):
            var start = index
            index += 1
            while index < text_len and is_ascii_digit(text[index]):
                index += 1
            token_count, out_len, ok = emit_word_tokens(
                text,
                start,
                index - start,
                dest,
                out_cap,
                out_len,
                offsets,
                lengths,
                max_tokens,
                token_count,
            )
            if not ok:
                token_count_out[0] = Int64(token_count)
                out_len_out[0] = Int64(out_len)
                return 1
        else:
            index += 1
    token_count_out[0] = Int64(token_count)
    out_len_out[0] = Int64(out_len)
    return 0


def project_hashed(
    values: F64Ptr,
    dim: Int,
    low: UInt32,
    high: UInt32,
    weight: Float64,
):
    var hashed = UInt64(Int(low)) | (UInt64(Int(high)) << 32)
    var position = Int(hashed % UInt64(dim))
    if (hashed & HASH_SIGN_BIT) != 0:
        values[position] += weight
    else:
        values[position] -= weight


def project_ngrams(
    values: F64Ptr,
    dim: Int,
    marked: U8Ptr,
    marked_len: Int,
    width: Int,
    weight: Float64,
):
    if marked_len < width:
        return
    var start = 0
    while start <= marked_len - width:
        var span = marked + start
        var low = crc32_update(span, width, NGRAM_PREFIX_LOW)
        var high = crc32_update(span, width, NGRAM_PREFIX_HIGH)
        project_hashed(values, dim, low, high, weight)
        start += 1


def embed_token(
    values: F64Ptr,
    dim: Int,
    token: U8Ptr,
    length: Int,
    frequency: Int,
    scratch: U8Ptr,
):
    var token_weight = 1.5 * sqrt(Float64(frequency))
    var low = crc32_update(token, length, TOKEN_PREFIX_LOW)
    var high = crc32_update(token, length, TOKEN_PREFIX_HIGH)
    project_hashed(values, dim, low, high, token_weight)

    scratch[0] = START_MARKER
    var i = 0
    while i < length:
        scratch[i + 1] = token[i]
        i += 1
    scratch[length + 1] = END_MARKER
    var marked_len = length + 2
    project_ngrams(values, dim, scratch, marked_len, 3, 0.45)
    project_ngrams(values, dim, scratch, marked_len, 4, 0.65)


def zero_f64(values: F64Ptr, count: Int):
    var zero = SIMD[DType.float64, F64W](0)
    var index = 0
    while index + F64W <= count:
        values.store(index, zero)
        index += F64W
    while index < count:
        values[index] = 0.0
        index += 1


def embed_bag(
    tokens: U8Ptr,
    offsets: I32Ptr,
    lengths: I32Ptr,
    freqs: I32Ptr,
    token_start: Int,
    token_end: Int,
    values: F64Ptr,
    dim: Int,
    scratch: U8Ptr,
):
    """Project one token bag into ``values``; leaves the vector unnormalized."""
    zero_f64(values, dim)
    var index = token_start
    while index < token_end:
        var length = Int(lengths[index])
        var frequency = Int(freqs[index])
        if length > 0 and frequency > 0:
            embed_token(
                values,
                dim,
                tokens + Int(offsets[index]),
                length,
                frequency,
                scratch,
            )
        index += 1


@export("mjj_static_embed")
def mjj_static_embed(
    tokens_addr: Int,
    offsets_addr: Int,
    lengths_addr: Int,
    freqs_addr: Int,
    token_count: Int,
    out_addr: Int,
    dim: Int,
) abi("C") -> Int:
    """Project unique tokens into an unnormalized float64 embedding.

    Caller applies CPython ``sum``-compatible L2 normalisation so persisted
    factors stay bit-compatible with the Python fallback.
    """
    if (
        tokens_addr == 0
        or offsets_addr == 0
        or lengths_addr == 0
        or freqs_addr == 0
        or out_addr == 0
        or dim <= 0
        or token_count < 0
    ):
        return 1
    var tokens = u8p(tokens_addr)
    var offsets = i32p(offsets_addr)
    var lengths = i32p(lengths_addr)
    var freqs = i32p(freqs_addr)
    var values = f64p(out_addr)
    var max_len = 0
    var index = 0
    while index < token_count:
        max_len = max(max_len, Int(lengths[index]))
        index += 1
    var scratch_buf = alloc[UInt8](max_len + 2)
    var scratch = u8p(Int(scratch_buf))
    embed_bag(
        tokens,
        offsets,
        lengths,
        freqs,
        0,
        token_count,
        values,
        dim,
        scratch,
    )
    scratch_buf.free()
    return 0


@export("mjj_static_embed_batch")
def mjj_static_embed_batch(
    tokens_addr: Int,
    offsets_addr: Int,
    lengths_addr: Int,
    freqs_addr: Int,
    bag_offsets_addr: Int,
    bag_count: Int,
    out_addr: Int,
    dim: Int,
) abi("C") -> Int:
    """Project many token bags into contiguous unnormalized float64 rows.

    ``bag_offsets`` is CSR-style ``Int32[bag_count + 1]`` into the shared
    offsets/lengths/freqs arrays. Output is row-major ``bag_count * dim``.
    Caller L2-normalises each row in CPython for factor bit-compatibility.
    """
    if bag_count < 0 or dim <= 0:
        return 1
    if bag_count == 0:
        return 0
    if (
        tokens_addr == 0
        or offsets_addr == 0
        or lengths_addr == 0
        or freqs_addr == 0
        or bag_offsets_addr == 0
        or out_addr == 0
    ):
        return 1
    var tokens = u8p(tokens_addr)
    var offsets = i32p(offsets_addr)
    var lengths = i32p(lengths_addr)
    var freqs = i32p(freqs_addr)
    var bag_offsets = i32p(bag_offsets_addr)
    var values = f64p(out_addr)
    var total_tokens = Int(bag_offsets[bag_count])
    if total_tokens < 0 or Int(bag_offsets[0]) != 0:
        return 1
    var max_len = 0
    var index = 0
    while index < total_tokens:
        max_len = max(max_len, Int(lengths[index]))
        index += 1
    var scratch_buf = alloc[UInt8](max_len + 2)
    var scratch = u8p(Int(scratch_buf))
    var bag = 0
    while bag < bag_count:
        var start = Int(bag_offsets[bag])
        var end = Int(bag_offsets[bag + 1])
        if start < 0 or end < start or end > total_tokens:
            scratch_buf.free()
            return 1
        embed_bag(
            tokens,
            offsets,
            lengths,
            freqs,
            start,
            end,
            values + bag * dim,
            dim,
            scratch,
        )
        bag += 1
    scratch_buf.free()
    return 0


@export("mjj_bm25_accumulate")
def mjj_bm25_accumulate(
    doc_ids_addr: Int,
    freqs_addr: Int,
    lengths_addr: Int,
    scores_addr: Int,
    posting_len: Int,
    average_length: Float64,
    k1: Float64,
    b: Float64,
    inverse_frequency: Float64,
    query_boost: Float64,
) abi("C") -> Int:
    """Accumulate one BM25 term into a dense float64 score buffer.

    Buffers are zero-copy ``array('q'|'d')`` addresses from Python. Exact with
    ``opt=0`` mojosub / CPython because the arithmetic is scalar and ordered.
    """
    if (
        posting_len <= 0
        or doc_ids_addr == 0
        or freqs_addr == 0
        or lengths_addr == 0
        or scores_addr == 0
        or average_length == 0.0
    ):
        return 0
    var doc_ids = i64p(doc_ids_addr)
    var freqs = i64p(freqs_addr)
    var lengths = f64p(lengths_addr)
    var scores = f64p(scores_addr)
    var index = 0
    while index < posting_len:
        var document_id = Int(doc_ids[index])
        var frequency = Float64(Int(freqs[index]))
        var normalizer = k1 * (
            1.0 - b + b * lengths[document_id] / average_length
        )
        scores[document_id] += (
            inverse_frequency
            * (frequency * (k1 + 1.0))
            / (frequency + normalizer)
            * query_boost
        )
        index += 1
    return posting_len


@export("mjj_quantize_i8")
def mjj_quantize_i8(
    values_addr: Int,
    output_addr: Int,
    count: Int,
    scale_out_addr: Int,
) abi("C") -> Int:
    """SIMD symmetric int8 quantise into int64 lanes (Python ``array('q')``).

    Writes the scale to ``scale_out_addr``. Matches the Python rounding rule:
    ``int(value * inverse ± 0.5)`` clamped to ``[-127, 127]``.
    """
    if values_addr == 0 or output_addr == 0 or scale_out_addr == 0 or count < 0:
        return 1
    var values = f64p(values_addr)
    var output = i64p(output_addr)
    var scale_out = f64p(scale_out_addr)
    if count == 0:
        scale_out[0] = 1.0
        return 0

    var peak_vec = SIMD[DType.float64, F64W](0)
    var index = 0
    while index + F64W <= count:
        peak_vec = max(peak_vec, abs(values.load[width=F64W](index)))
        index += F64W
    var peak = peak_vec.reduce_max()
    while index < count:
        peak = max(peak, abs(values[index]))
        index += 1

    var scale = peak / 127.0 if peak else 1.0
    var inverse = 1.0 / scale
    var lo = SIMD[DType.int64, F64W](-127)
    var hi = SIMD[DType.int64, F64W](127)
    index = 0
    while index + F64W <= count:
        var v = values.load[width=F64W](index)
        var bias = copysign(SIMD[DType.float64, F64W](0.5), v)
        var rounded = (v * inverse + bias).cast[DType.int64]()
        output.store(index, min(max(rounded, lo), hi))
        index += F64W
    while index < count:
        var value = values[index]
        var rounded = Int(value * inverse + (0.5 if value >= 0.0 else -0.5))
        if rounded < -127:
            rounded = -127
        elif rounded > 127:
            rounded = 127
        output[index] = Int64(rounded)
        index += 1
    scale_out[0] = scale
    return 0
