"""Zero-copy int8 top-k scan used by MJJ's mmap search index."""

from std.algorithm import parallelize
from std.sys.info import num_performance_cores, simd_width_of as simdwidthof

comptime F32Ptr = UnsafePointer[Float32, AnyOrigin[mut=True]]
comptime I8Ptr = UnsafePointer[Int8, AnyOrigin[mut=True]]
comptime I64Ptr = UnsafePointer[Int64, AnyOrigin[mut=True]]
comptime I8W = simdwidthof[DType.int8]()
comptime PARALLEL_WORK_THRESHOLD = 100_000_000


def f32p(addr: Int) -> F32Ptr:
    return F32Ptr(unsafe_from_address=addr)


def i8p(addr: Int) -> I8Ptr:
    return I8Ptr(unsafe_from_address=addr)


def i64p(addr: Int) -> I64Ptr:
    return I64Ptr(unsafe_from_address=addr)


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
