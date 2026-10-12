#!/usr/bin/env python3
"""
Polaris Vector Memory Engine - ChromaDB Layer
Handles semantic memory: embeddings, vector storage, and context retrieval
"""

import chromadb
from typing import List, Dict, Any, Optional
import requests
from datetime import datetime

# ChromaDB persistence path - mounted volume
CHROMA_DB_PATH = "/data/polaris_vector_db"

# Ollama embedding endpoint
OLLAMA_EMBEDDING_MODEL = "nomic-embed-text"
OLLAMA_BASE_URL = "http://ollama:11434"


class PolarisVectorMemory:
    """ChromaDB vector memory manager for Polaris Gateway semantic search."""
    
    def __init__(self, persist_directory: str = CHROMA_DB_PATH):
        self.persist_directory = persist_directory
        self.client = None
        self.collections = {}
        self._init_chromadb()
    
    def _init_chromadb(self):
        """Initialize ChromaDB with persistent storage."""
        try:
            self.client = chromadb.PersistentClient(path=self.persist_directory)
            
            # Create or get collections
            self.collections['global_knowledge'] = self._get_or_create_collection(
                'global_knowledge',
                metadata={"description": "Global knowledge base for all users"}
            )
            
            self.collections['user_memories'] = self._get_or_create_collection(
                'user_memories',
                metadata={"description": "Per-user semantic memories"}
            )
            
            self.collections['conversation_context'] = self._get_or_create_collection(
                'conversation_context',
                metadata={"description": "Recent conversation context for continuity"}
            )
            
            self.collections['vault_documents'] = self._get_or_create_collection(
                'vault_documents',
                metadata={"description": "Memory Vault markdown document index"}
            )
            
            print(f"[VectorMemory] Initialized ChromaDB at {self.persist_directory}")
        except Exception as e:
            print(f"[VectorMemory] Failed to initialize ChromaDB: {e}")
            raise
    
    def _get_or_create_collection(self, name: str, metadata: Optional[Dict] = None) -> chromadb.Collection:
        """Get existing collection or create new one."""
        try:
            # Try to get the collection first
            return self.client.get_collection(name=name)
        except Exception as get_error:
            # If getting fails, try to create it
            try:
                return self.client.create_collection(name=name, metadata=metadata)
            except Exception as create_error:
                # If create fails because it already exists, get it
                if "already exists" in str(create_error).lower():
                    return self.client.get_collection(name=name)
                # Otherwise raise the original error
                raise get_error
    
    def _generate_embedding(self, text: str) -> List[float]:
        """Generate embedding vector using Ollama."""
        try:
            response = requests.post(
                f"{OLLAMA_BASE_URL}/api/embeddings",
                json={
                    "model": OLLAMA_EMBEDDING_MODEL,
                    "prompt": text
                },
                timeout=30
            )
            response.raise_for_status()
            return response.json().get('embedding', [])
        except Exception as e:
            print(f"[VectorMemory] Embedding generation failed: {e}")
            return []
    
    def _generate_batch_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple texts."""
        embeddings = []
        for text in texts:
            embedding = self._generate_embedding(text)
            if embedding:
                embeddings.append(embedding)
            else:
                # Fallback: zero vector if embedding fails
                embeddings.append([0.0] * 768)  # nomic-embed-text dimension
        return embeddings
    
    # ========== GLOBAL KNOWLEDGE BASE ==========
    
    def add_to_global_knowledge(self, document_id: str, content: str, 
                               metadata: Optional[Dict] = None) -> bool:
        """Add document to global knowledge base."""
        try:
            collection = self.collections['global_knowledge']
            embedding = self._generate_embedding(content)
            
            if not embedding:
                return False
            
            full_metadata = {
                **(metadata or {}),
                'content': content,
                'added_at': datetime.now().isoformat(),
                'source': 'global_knowledge'
            }
            
            collection.add(
                ids=[document_id],
                embeddings=[embedding],
                metadatas=[full_metadata]
            )
            return True
        except Exception as e:
            print(f"[VectorMemory] Failed to add to global knowledge: {e}")
            return False
    
    def search_global_knowledge(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Search global knowledge base."""
        try:
            collection = self.collections['global_knowledge']
            query_embedding = self._generate_embedding(query)
            
            if not query_embedding:
                return []
            
            results = collection.query(
                query_embeddings=[query_embedding],
                n_results=limit,
                include=['metadatas', 'distances']
            )
            
            return self._format_results(results)
        except Exception as e:
            print(f"[VectorMemory] Global knowledge search failed: {e}")
            return []
    
    # ========== USER-SPECIFIC MEMORIES ==========
    
    def add_user_memory(self, user_id: str, memory_id: str, content: str,
                       memory_type: str = 'fact', metadata: Optional[Dict] = None) -> bool:
        """Add memory specific to a user."""
        try:
            collection = self.collections['user_memories']
            embedding = self._generate_embedding(content)
            
            if not embedding:
                return False
            
            full_metadata = {
                **(metadata or {}),
                'user_id': user_id,
                'memory_type': memory_type,
                'content': content,
                'added_at': datetime.now().isoformat(),
                'source': 'user_memory'
            }
            
            collection.add(
                ids=[f"{user_id}_{memory_id}"],
                embeddings=[embedding],
                metadatas=[full_metadata]
            )
            return True
        except Exception as e:
            print(f"[VectorMemory] Failed to add user memory: {e}")
            return False
    
    def search_user_memories(self, user_id: str, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Search memories specific to a user."""
        try:
            collection = self.collections['user_memories']
            query_embedding = self._generate_embedding(query)
            
            if not query_embedding:
                return []
            
            results = collection.query(
                query_embeddings=[query_embedding],
                n_results=limit,
                where={'user_id': user_id},
                include=['metadatas', 'distances']
            )
            
            return self._format_results(results)
        except Exception as e:
            print(f"[VectorMemory] User memory search failed: {e}")
            return []
    
    def get_all_user_memories(self, user_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Get all memories for a user."""
        try:
            collection = self.collections['user_memories']
            results = collection.get(
                where={'user_id': user_id},
                limit=limit,
                include=['metadatas']
            )
            
            return self._format_results(results, is_query=False)
        except Exception as e:
            print(f"[VectorMemory] Failed to get user memories: {e}")
            return []
    
    def delete_user_memory(self, user_id: str, memory_id: str) -> bool:
        """Delete a specific user memory."""
        try:
            collection = self.collections['user_memories']
            collection.delete(ids=[f"{user_id}_{memory_id}"])
            return True
        except Exception as e:
            print(f"[VectorMemory] Failed to delete user memory: {e}")
            return False
    
    # ========== CONVERSATION CONTEXT ==========
    
    def add_conversation_context(self, session_id: str, context_id: str, 
                                content: str, metadata: Optional[Dict] = None) -> bool:
        """Add conversation context for session continuity."""
        try:
            collection = self.collections['conversation_context']
            embedding = self._generate_embedding(content)
            
            if not embedding:
                return False
            
            full_metadata = {
                **(metadata or {}),
                'session_id': session_id,
                'content': content,
                'added_at': datetime.now().isoformat(),
                'source': 'conversation_context'
            }
            
            collection.add(
                ids=[f"{session_id}_{context_id}"],
                embeddings=[embedding],
                metadatas=[full_metadata]
            )
            return True
        except Exception as e:
            print(f"[VectorMemory] Failed to add conversation context: {e}")
            return False
    
    def search_conversation_context(self, session_id: str, query: str, 
                                   limit: int = 10) -> List[Dict[str, Any]]:
        """Search conversation context within a session."""
        try:
            collection = self.collections['conversation_context']
            query_embedding = self._generate_embedding(query)
            
            if not query_embedding:
                return []
            
            results = collection.query(
                query_embeddings=[query_embedding],
                n_results=limit,
                where={'session_id': session_id},
                include=['metadatas', 'distances']
            )
            
            return self._format_results(results)
        except Exception as e:
            print(f"[VectorMemory] Conversation context search failed: {e}")
            return []
    
    def clear_conversation_context(self, session_id: str) -> bool:
        """Clear all context for a session."""
        try:
            collection = self.collections['conversation_context']
            existing = collection.get(where={'session_id': session_id}, include=[])
            if existing and existing['ids']:
                collection.delete(ids=existing['ids'])
            return True
        except Exception as e:
            print(f"[VectorMemory] Failed to clear conversation context: {e}")
            return False
    
    # ========== ADVANCED SEMANTIC SEARCH ==========
    
    def hybrid_search(self, query: str, user_id: Optional[str] = None,
                     search_global: bool = True, search_user: bool = True,
                     search_context: bool = False, session_id: Optional[str] = None,
                     limit_per_source: int = 5) -> List[Dict[str, Any]]:
        """Perform hybrid search across multiple memory sources."""
        all_results = []
        
        if search_global:
            global_results = self.search_global_knowledge(query, limit_per_source)
            all_results.extend(global_results)
        
        if search_user and user_id:
            user_results = self.search_user_memories(user_id, query, limit_per_source)
            all_results.extend(user_results)
        
        if search_context and session_id:
            context_results = self.search_conversation_context(
                session_id, query, limit_per_source
            )
            all_results.extend(context_results)
        
        # Sort by relevance (distance)
        all_results.sort(key=lambda x: x.get('distance', float('inf')))
        return all_results
    
    def build_context_prompt(self, query: str, user_id: Optional[str] = None,
                            session_id: Optional[str] = None,
                            max_context_items: int = 10) -> str:
        """Build enriched context prompt from semantic search."""
        results = self.hybrid_search(
            query=query,
            user_id=user_id,
            search_global=True,
            search_user=True if user_id else False,
            search_context=True if session_id else False,
            session_id=session_id,
            limit_per_source=max_context_items // 3 + 1
        )
        
        if not results:
            return ""
        
        context_parts = []
        for i, result in enumerate(results[:max_context_items], 1):
            metadata = result.get('metadata', {})
            content = metadata.get('content', '')
            source = metadata.get('source', 'unknown')
            
            if content:
                context_parts.append(f"[{i}] ({source}) {content}")
        
        if context_parts:
            return "RELEVANT CONTEXT:\n" + "\n".join(context_parts) + "\n"
        return ""
    
    # ========== UTILITY METHODS ==========
    
    def _format_results(self, chroma_results: Dict, is_query: bool = True) -> List[Dict[str, Any]]:
        """Format ChromaDB results into standardized format."""
        formatted = []
        
        if is_query:
            distances = chroma_results.get('distances', [[]])[0]
            metadatas = chroma_results.get('metadatas', [[]])[0]
            ids = chroma_results.get('ids', [[]])[0]
            
            for i, metadata in enumerate(metadatas):
                formatted.append({
                    'id': ids[i] if i < len(ids) else None,
                    'metadata': metadata,
                    'distance': distances[i] if i < len(distances) else None,
                    'relevance_score': 1.0 / (1.0 + distances[i]) if i < len(distances) else 0.0
                })
        else:
            metadatas = chroma_results.get('metadatas', [])
            ids = chroma_results.get('ids', [])
            
            for i, metadata in enumerate(metadatas):
                formatted.append({
                    'id': ids[i] if i < len(ids) else None,
                    'metadata': metadata,
                    'distance': None,
                    'relevance_score': None
                })
        
        return formatted
    
    def get_collection_stats(self) -> Dict[str, Dict[str, Any]]:
        """Get statistics for all collections."""
        stats = {}
        for name, collection in self.collections.items():
            count = collection.count()
            stats[name] = {
                'document_count': count,
                'collection_name': name
            }
        return stats
    
    def cleanup_old_context(self, session_id: str, max_age_hours: int = 24) -> int:
        """Remove conversation context older than specified hours."""
        try:
            collection = self.collections['conversation_context']
            results = collection.get(
                where={'session_id': session_id},
                include=['metadatas']
            )
            
            if not results or not results.get('metadatas'):
                return 0
            
            cutoff = datetime.now().timestamp() - (max_age_hours * 3600)
            ids_to_delete = []
            
            for metadata in results['metadatas']:
                added_at = metadata.get('added_at', '')
                if added_at:
                    try:
                        timestamp = datetime.fromisoformat(added_at).timestamp()
                        if timestamp < cutoff:
                            ids_to_delete.append(metadata.get('id', ''))
                    except:
                        pass
            
            if ids_to_delete:
                collection.delete(ids=ids_to_delete)
                return len(ids_to_delete)
            
            return 0
        except Exception as e:
            print(f"[VectorMemory] Cleanup failed: {e}")
            return 0


# Singleton instance
_vector_memory_instance: Optional[PolarisVectorMemory] = None


def get_vector_memory() -> PolarisVectorMemory:
    """Get or create the singleton VectorMemory instance."""
    global _vector_memory_instance
    if _vector_memory_instance is None:
        _vector_memory_instance = PolarisVectorMemory()
    return _vector_memory_instance