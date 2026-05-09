import json
import faiss
import numpy as np
from sentence_transformers import SentenceTransformer
import os
from typing import List, Dict

# Model choice: Lightweight and fast
MODEL_NAME = 'all-MiniLM-L6-v2'

class VectorStore:
    def __init__(self, catalog_path: str = 'data/catalog.json', index_path: str = 'data/faiss_index'):
        self.catalog_path = catalog_path
        self.index_path = index_path
        self.products = []
        self.index = None
        self._model = None # Lazy loaded
        
    @property
    def model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(MODEL_NAME)
        return self._model
        
    def load_catalog(self):
        if not os.path.exists(self.catalog_path):
            print(f"Error: {self.catalog_path} not found.")
            return
            
        with open(self.catalog_path, 'r', encoding='utf-8') as f:
            raw_data = json.load(f, strict=False)
            self.products = []
            for item in raw_data:
                item['url'] = item.get('url') or item.get('link') or "#"
                # Preserve all category keys for high-fidelity grounding
                item['categories'] = item.get('keys', [])
                
                self.products.append(item)
            print(f"Loaded {len(self.products)} products from official catalog.")
            
    def build_index(self):
        if not self.products:
            self.load_catalog()
            
        print("Building vector index...")
        # Index Name, Description, and Metadata together for deep semantic matching
        texts = []
        for p in self.products:
            text = f"Name: {p.get('name', '')}. "
            text += f"Levels: {p.get('job_levels', '')}. "
            text += f"Category: {p.get('keys', '')}. "
            text += f"Duration: {p.get('duration_raw', '')}. "
            text += f"Remote: {p.get('remote', '')}. "
            text += f"Adaptive: {p.get('adaptive', '')}. "
            text += f"Languages: {p.get('languages_raw', '')}. "
            text += f"Status: {p.get('status', '')}. "
            text += f"Description: {p.get('description', '')}"
            texts.append(text)
            
        embeddings = self.model.encode(texts)
        # Use NumPy for normalization to avoid FAISS linter issues
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        embeddings = embeddings / (norms + 1e-10) # Add small epsilon to avoid divide by zero
        
        dimension = embeddings.shape[1]
        self.index = faiss.IndexFlatIP(dimension) # Use Inner Product (IP) for Cosine Similarity
        x_embeddings = np.array(embeddings).astype('float32')
        self.index.add(x_embeddings)
        print(f"Indexed {len(self.products)} products with full context.")
        
    def search(self, query: str, k: int = 10) -> List[Dict]:
        if self.index is None:
            self.build_index()
            
        query_vec = self.model.encode([query])
        query_vec = query_vec / (np.linalg.norm(query_vec) + 1e-10)
        x_query = np.array(query_vec).astype('float32')
        
        distances, indices = self.index.search(x_query, k)
        
        results = []
        for dist, idx in zip(distances[0], indices[0]):
            if idx != -1 and idx < len(self.products):
                results.append(self.products[idx])
                
        return results

# Singleton instance
_instance = None
def get_vector_store():
    global _instance
    if _instance is None:
        _instance = VectorStore()
        _instance.load_catalog()
    return _instance
