from utils.vector_store import Retriever, get_vector_env_status


def test_vector_env_status():
    status = get_vector_env_status()
    assert 'embeddings' in status
    assert 'faiss' in status
    assert status['dim'] == 384


def test_retriever_fallback_search():
    r = Retriever()
    texts = ["Alpha company strong growth", "Beta risk elevated", "Gamma stable margins"]
    kinds = ["message"] * len(texts)
    r.build(texts, kinds)
    results = r.search("growth", top_k=2)
    assert len(results) <= 2
    assert any("Alpha" in itm.text for itm in results)
