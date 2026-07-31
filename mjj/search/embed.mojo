"""Optional repository-local wrapper for scanning mmap-backed MJJ indexes.

Build with ``mojo build mjj/search/embed.mojo -I ../mojo-embed/src`` when a
standalone library is wanted.  Normal installs use mojo-embed's already
exported ``embed_search_i8`` symbol, which accepts the same mmap pointers, and
never wait for this file to compile.
"""

from embed.capi import search_i8_impl


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
    search_i8_impl(
        data_addr,
        factors_addr,
        ids_addr,
        query_addr,
        scores_addr,
        output_ids_addr,
        output_scores_addr,
        count,
        dim,
        k,
        query_factor,
        True,
    )
