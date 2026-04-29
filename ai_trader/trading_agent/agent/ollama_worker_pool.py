"""
Ollama Worker Pool - Parallel inference with multiple model instances.

Manages a pool of lightweight Ollama models (e.g., qwen2.5:7b) for parallel
data enrichment tasks, while keeping a separate analysis model (e.g., qwen3:30b)
for high-quality summaries.

Architecture:
- Analysis tier: 1x qwen3:30b-a3b (~10GB) for final summaries
- Worker tier: 3x qwen2.5:7b (~13.5GB) for parallel enrichment
- Total: ~23.5GB / 24GB on RTX 3090

Usage:
    pool = OllamaWorkerPool(
        analysis_model="qwen3:30b-a3b",
        worker_model="qwen2.5:7b",
        num_workers=3
    )
    
    # Warmup at 6:30 AM
    pool.warmup()
    
    # Parallel processing
    results = pool.process_parallel(items, process_func)
    
    # Shutdown at 4:00 PM
    pool.shutdown()
"""

import logging
import time
import requests
from typing import List, Dict, Any, Callable, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class WorkerStats:
    """Statistics for a worker instance."""
    worker_id: int
    requests_processed: int = 0
    total_time_seconds: float = 0.0
    errors: int = 0
    last_used: Optional[float] = None


class OllamaWorkerPool:
    """
    Manages multiple Ollama model instances for parallel inference.
    """

    def __init__(
        self,
        analysis_model: str = "qwen3:30b-a3b",
        worker_model: str = "qwen2.5:7b",
        num_workers: int = 3,
        base_url: str = "http://localhost:11434",
        timeout_seconds: int = 60,
        keep_alive_hours: int = 8
    ):
        """
        Initialize worker pool.

        Args:
            analysis_model: High-quality model for final analysis
            worker_model: Fast model for parallel enrichment tasks
            num_workers: Number of worker instances (default 3 for RTX 3090)
            base_url: Ollama server URL
            timeout_seconds: Request timeout per worker
            keep_alive_hours: How long to keep models loaded (default 8h = trading day)
        """
        self.analysis_model = analysis_model
        self.worker_model = worker_model
        self.num_workers = num_workers
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout_seconds
        self.keep_alive_seconds = keep_alive_hours * 3600

        self._executor: Optional[ThreadPoolExecutor] = None
        self._worker_stats = [WorkerStats(worker_id=i) for i in range(num_workers)]
        self._is_warmed_up = False

        logger.info(
            f"OllamaWorkerPool initialized: "
            f"analysis={analysis_model}, workers={num_workers}x{worker_model}"
        )

    @classmethod
    def check_availability(
        cls,
        analysis_model: str,
        worker_model: str,
        base_url: str = "http://localhost:11434",
        perplexity_available: bool = False
    ) -> Dict[str, Any]:
        """
        Check if Ollama is running and which models are available.

        Args:
            analysis_model: Preferred analysis model (e.g., qwen3:14b)
            worker_model: Preferred worker model (e.g., qwen2.5:7b)
            base_url: Ollama server URL
            perplexity_available: If True, analysis model not needed (Perplexity handles enrichment)

        Returns dict with:
        - available: bool (True if Ollama running and suitable model(s) exist)
        - analysis_model_found: bool
        - worker_model_found: bool
        - analysis_model_actual: str (model name to use for analysis)
        - worker_model_actual: str (model name to use for workers)
        - available_models: list of all models found
        - mode: str ('dual', 'single', 'workers-only', 'none')

        Smart fallback logic:
        - Perplexity available: disabled (Perplexity handles all enrichment, backtesting is pure Python)
        - Both models (no Perplexity): dual mode (workers do parallel enrichment)
        - Only 14b (no Perplexity): single mode (use 14b for both analysis and enrichment)
        - Only 7b (no Perplexity): disabled (7b too weak for enrichment analysis)
        - No models: disabled
        """
        result = {
            'available': False,
            'analysis_model_found': False,
            'worker_model_found': False,
            'analysis_model_actual': analysis_model,
            'worker_model_actual': worker_model,
            'available_models': [],
            'mode': 'none'
        }

        try:
            response = requests.get(f"{base_url.rstrip('/')}/api/tags", timeout=5)
            if response.status_code != 200:
                logger.warning(f"Ollama not reachable at {base_url}: HTTP {response.status_code}")
                return result

            data = response.json()
            models = [m.get("name", "") for m in data.get("models", [])]
            result['available_models'] = models

            # Check exact model matches
            analysis_found = analysis_model in models
            worker_found = worker_model in models

            result['analysis_model_found'] = analysis_found
            result['worker_model_found'] = worker_found

            # Smart fallback logic
            if perplexity_available:
                # Perplexity handles ALL enrichment, backtesting is pure Python
                # Workers not needed - save VRAM
                result['available'] = False
                result['mode'] = 'none'
                logger.info(
                    "Ollama: Perplexity enabled - worker pool disabled. "
                    "Perplexity handles enrichment, backtesting is pure Python. Minimal VRAM mode (~2GB)."
                )

            elif analysis_found and worker_found:
                # Both models exist - use as configured (Brave+Ollama fallback mode)
                result['available'] = True
                result['mode'] = 'dual'
                logger.info(
                    f"Ollama: Both models available ({analysis_model} + {worker_model}). "
                    f"Fallback mode for when Perplexity unavailable."
                )

            elif analysis_found and not worker_found:
                # Only analysis model - use it for both (Brave+Ollama fallback mode)
                result['available'] = True
                result['worker_model_actual'] = analysis_model
                result['mode'] = 'single'
                logger.info(
                    f"Ollama: Only {analysis_model} available, using for both analysis and workers. "
                    f"Fallback mode for when Perplexity unavailable."
                )

            elif worker_found and not analysis_found:
                # Only worker model - too weak for enrichment without Perplexity
                result['available'] = False
                result['mode'] = 'none'
                logger.warning(
                    f"Ollama: Only {worker_model} available (too weak for enrichment). "
                    f"Worker pool disabled. Either: (1) set PERPLEXITY_API_KEY, or (2) ollama pull {analysis_model}"
                )

            else:
                # Neither model found
                logger.info(f"Ollama running but required models not found. Available: {models}")

        except requests.exceptions.RequestException as e:
            logger.info(f"Ollama not available at {base_url}: {e}")
        except Exception as e:
            logger.warning(f"Error checking Ollama availability: {e}")

        return result

    def warmup(self) -> bool:
        """
        Load all models into VRAM with long keepalive.

        Performs warmup requests to:
        1. Load analysis model into VRAM
        2. Load worker models into VRAM (parallel)
        3. Set keepalive to cover full trading day

        Returns:
            True if all models loaded successfully
        """
        if self._is_warmed_up:
            logger.info("Worker pool already warmed up")
            return True

        logger.info(f"Warming up worker pool (analysis + {self.num_workers} workers)...")
        start_time = time.time()

        try:
            # Warmup analysis model first
            logger.info(f"Loading analysis model: {self.analysis_model}")
            if not self._warmup_model(self.analysis_model):
                logger.error(f"Failed to load analysis model: {self.analysis_model}")
                return False

            # Warmup worker models in parallel
            logger.info(f"Loading {self.num_workers} worker instances: {self.worker_model}")
            with ThreadPoolExecutor(max_workers=self.num_workers) as executor:
                futures = [
                    executor.submit(self._warmup_model, self.worker_model, worker_id=i)
                    for i in range(self.num_workers)
                ]
                
                results = [f.result() for f in as_completed(futures)]
                
                if not all(results):
                    logger.error("Failed to load one or more worker models")
                    return False

            elapsed = time.time() - start_time
            self._is_warmed_up = True
            self._executor = ThreadPoolExecutor(max_workers=self.num_workers)

            logger.info(
                f"Worker pool warmup complete in {elapsed:.1f}s "
                f"(keepalive={self.keep_alive_seconds/3600:.1f}h)"
            )
            return True

        except Exception as e:
            logger.error(f"Worker pool warmup failed: {e}")
            return False

    def shutdown(self):
        """
        Unload all models from VRAM and shutdown thread pool.

        Call this at end of trading day to free GPU memory.
        """
        logger.info("Shutting down worker pool...")

        try:
            # Unload all models
            self._unload_model(self.analysis_model)
            self._unload_model(self.worker_model)

            # Shutdown executor
            if self._executor:
                self._executor.shutdown(wait=True)
                self._executor = None

            self._is_warmed_up = False
            logger.info("Worker pool shutdown complete")

        except Exception as e:
            logger.error(f"Worker pool shutdown error: {e}")

    def process_parallel(
        self,
        items: List[Any],
        process_func: Callable[[Any, int], Any],
        use_analysis_for_final: bool = False
    ) -> List[Any]:
        """
        Process items in parallel using worker pool.

        Args:
            items: List of items to process
            process_func: Function(item, worker_id) -> result
            use_analysis_for_final: If True, use analysis model for final summary

        Returns:
            List of results (same order as items)
        """
        if not self._is_warmed_up:
            logger.warning("Worker pool not warmed up - warming up now...")
            if not self.warmup():
                logger.error("Failed to warmup worker pool - falling back to sequential")
                return [process_func(item, 0) for item in items]

        if not items:
            return []

        logger.info(f"Processing {len(items)} items in parallel with {self.num_workers} workers")
        start_time = time.time()

        results = [None] * len(items)  # Preserve order
        
        try:
            # Submit all tasks
            futures = {}
            for idx, item in enumerate(items):
                worker_id = idx % self.num_workers
                future = self._executor.submit(self._process_with_stats, item, process_func, worker_id)
                futures[future] = idx

            # Collect results as they complete
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    results[idx] = future.result()
                except Exception as e:
                    logger.error(f"Worker processing error for item {idx}: {e}")
                    results[idx] = None

            elapsed = time.time() - start_time
            successful = sum(1 for r in results if r is not None)
            
            logger.info(
                f"Parallel processing complete: {successful}/{len(items)} successful "
                f"in {elapsed:.1f}s ({len(items)/elapsed:.1f} items/sec)"
            )

            return results

        except Exception as e:
            logger.error(f"Parallel processing failed: {e}")
            return results

    def _process_with_stats(self, item: Any, process_func: Callable, worker_id: int) -> Any:
        """Process item and track stats."""
        start_time = time.time()
        try:
            result = process_func(item, worker_id)
            elapsed = time.time() - start_time
            
            stats = self._worker_stats[worker_id]
            stats.requests_processed += 1
            stats.total_time_seconds += elapsed
            stats.last_used = time.time()
            
            return result
        except Exception as e:
            self._worker_stats[worker_id].errors += 1
            raise

    def complete_with_worker(
        self,
        prompt: str,
        worker_id: int = 0,
        system: Optional[str] = None
    ) -> Optional[str]:
        """
        Send prompt to a specific worker instance.

        Args:
            prompt: User prompt
            worker_id: Which worker to use (0 to num_workers-1)
            system: Optional system prompt

        Returns:
            Response text or None on error
        """
        try:
            payload = {
                "model": self.worker_model,
                "prompt": prompt,
                "stream": False,
                "keep_alive": self.keep_alive_seconds,
                "options": {
                    "temperature": 0.1,
                    "num_predict": 2048
                }
            }
            
            if system:
                payload["system"] = system

            response = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=self.timeout
            )

            if response.status_code != 200:
                logger.error(f"Worker {worker_id} error: HTTP {response.status_code}")
                return None

            data = response.json()
            return data.get("response", "").strip()

        except Exception as e:
            logger.error(f"Worker {worker_id} request failed: {e}")
            return None

    def complete_with_analysis(
        self,
        prompt: str,
        system: Optional[str] = None
    ) -> Optional[str]:
        """
        Send prompt to analysis model (high quality).

        Args:
            prompt: User prompt
            system: Optional system prompt

        Returns:
            Response text or None on error
        """
        try:
            payload = {
                "model": self.analysis_model,
                "prompt": prompt,
                "stream": False,
                "keep_alive": self.keep_alive_seconds,
                "options": {
                    "temperature": 0.1,
                    "num_predict": 4096
                }
            }
            
            if system:
                payload["system"] = system

            response = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=self.timeout * 2  # Analysis model may be slower
            )

            if response.status_code != 200:
                logger.error(f"Analysis model error: HTTP {response.status_code}")
                return None

            data = response.json()
            return data.get("response", "").strip()

        except Exception as e:
            logger.error(f"Analysis model request failed: {e}")
            return None

    def _warmup_model(self, model: str, worker_id: Optional[int] = None) -> bool:
        """Warmup a single model instance."""
        try:
            payload = {
                "model": model,
                "prompt": "Ready.",
                "stream": False,
                "keep_alive": self.keep_alive_seconds
            }
            
            # Use longer timeout for warmup (models need time to load into VRAM)
            warmup_timeout = max(self.timeout * 2, 180)  # At least 3 minutes
            
            response = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=warmup_timeout
            )
            
            if response.status_code == 200:
                worker_info = f" (worker {worker_id})" if worker_id is not None else ""
                logger.info(f"Model loaded: {model}{worker_info}")
                return True
            else:
                logger.error(f"Model load failed: {model} - HTTP {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"Model warmup error ({model}): {e}")
            return False

    def _unload_model(self, model: str):
        """Unload a model from VRAM."""
        try:
            payload = {
                "model": model,
                "prompt": "",
                "keep_alive": 0
            }
            
            response = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=10
            )
            
            if response.status_code == 200:
                logger.info(f"Model unloaded: {model}")
            else:
                logger.warning(f"Model unload returned HTTP {response.status_code}: {model}")
                
        except Exception as e:
            logger.warning(f"Model unload error ({model}): {e}")

    def get_stats(self) -> Dict[str, Any]:
        """Get worker pool statistics."""
        total_requests = sum(w.requests_processed for w in self._worker_stats)
        total_time = sum(w.total_time_seconds for w in self._worker_stats)
        total_errors = sum(w.errors for w in self._worker_stats)
        
        return {
            "is_warmed_up": self._is_warmed_up,
            "num_workers": self.num_workers,
            "analysis_model": self.analysis_model,
            "worker_model": self.worker_model,
            "total_requests": total_requests,
            "total_time_seconds": total_time,
            "total_errors": total_errors,
            "avg_time_per_request": total_time / total_requests if total_requests > 0 else 0,
            "workers": [
                {
                    "worker_id": w.worker_id,
                    "requests": w.requests_processed,
                    "errors": w.errors,
                    "avg_time": w.total_time_seconds / w.requests_processed if w.requests_processed > 0 else 0,
                    "last_used": w.last_used
                }
                for w in self._worker_stats
            ]
        }

    def is_available(self) -> bool:
        """Check if worker pool is ready."""
        return self._is_warmed_up and self._executor is not None
    
    def health_check(self) -> bool:
        """
        Perform health check on worker pool.
        
        Checks if Ollama is still running and models are loaded.
        Automatically attempts recovery if issues detected.
        
        Returns:
            True if healthy, False if unhealthy (after recovery attempt)
        """
        if not self._is_warmed_up:
            return False
        
        try:
            # Quick ping to Ollama
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            if response.status_code != 200:
                logger.warning("Ollama health check failed - attempting recovery")
                return self._attempt_recovery()
            
            return True
            
        except requests.exceptions.ConnectionError:
            logger.warning("Ollama connection lost - attempting recovery")
            return self._attempt_recovery()
        except Exception as e:
            logger.error(f"Health check error: {e}")
            return False
    
    def _attempt_recovery(self) -> bool:
        """
        Attempt to recover from Ollama crash/restart.
        
        Returns:
            True if recovery successful, False otherwise
        """
        logger.info("Attempting worker pool recovery...")
        
        # Mark as not warmed up
        self._is_warmed_up = False
        
        # Wait briefly for Ollama to stabilize
        time.sleep(2)
        
        # Attempt rewarm
        if self.warmup():
            logger.info("✓ Worker pool recovery successful!")
            return True
        else:
            logger.error("✗ Worker pool recovery failed")
            return False
    
    def complete_with_worker_retry(
        self,
        prompt: str,
        worker_id: int = 0,
        system: Optional[str] = None,
        max_retries: int = 2
    ) -> Optional[str]:
        """
        Send prompt to worker with automatic retry on failure.
        
        Args:
            prompt: User prompt
            worker_id: Which worker to use
            system: Optional system prompt
            max_retries: Maximum retry attempts
            
        Returns:
            Response text or None on error
        """
        for attempt in range(max_retries + 1):
            result = self.complete_with_worker(prompt, worker_id, system)
            if result is not None:
                return result
            
            if attempt < max_retries:
                logger.warning(f"Worker {worker_id} failed (attempt {attempt + 1}/{max_retries + 1}), retrying...")
                
                # Check health and attempt recovery if needed
                if not self.health_check():
                    logger.error(f"Health check failed, cannot retry")
                    return None
                
                time.sleep(1)  # Brief delay before retry
        
        logger.error(f"Worker {worker_id} failed after {max_retries + 1} attempts")
        return None

    def __enter__(self):
        """Context manager entry."""
        self.warmup()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.shutdown()
