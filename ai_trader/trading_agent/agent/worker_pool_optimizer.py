"""
Worker Pool Self-Healing Optimizer

Analyzes daily logs for OOM errors, crashes, and performance issues.
Automatically adjusts worker count if problems detected.
Uses Ollama 30b model for intelligent log analysis.

Usage:
    optimizer = WorkerPoolOptimizer(ollama_provider)
    optimizer.analyze_and_optimize()  # Run at end of day
"""

import logging
import json
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List
import re

logger = logging.getLogger(__name__)


class WorkerPoolOptimizer:
    """Self-healing optimizer for worker pool configuration."""

    def __init__(self, ollama_provider=None):
        """
        Initialize optimizer.
        
        Args:
            ollama_provider: OllamaProvider for log analysis
        """
        self.ollama = ollama_provider
        self.config_path = Path(__file__).parent.parent.parent / "ai_trader_data" / "broker_config.json"
        self.optimization_log_path = Path(__file__).parent.parent.parent / "ai_trader_data" / "worker_pool_optimizations.json"
        
    def analyze_and_optimize(self) -> Dict:
        """
        Analyze today's logs and optimize worker pool if needed.
        
        Returns:
            Dict with analysis results and actions taken
        """
        logger.info("Starting worker pool self-healing analysis...")
        
        # Get today's log file
        log_content = self._get_todays_logs()
        if not log_content:
            logger.warning("No log content found for analysis")
            return {"status": "no_logs", "action": None}
        
        # Extract error patterns
        issues = self._extract_error_patterns(log_content)
        
        if not issues['has_issues']:
            logger.info("✓ No worker pool issues detected")
            return {
                "status": "healthy",
                "action": None,
                "analysis": "No OOM or crash patterns found"
            }
        
        # Use Ollama to analyze severity
        analysis = self._analyze_with_ollama(log_content, issues)
        if not analysis:
            logger.warning("Ollama analysis unavailable, using pattern-based decision")
            analysis = self._fallback_analysis(issues)
        
        # Make optimization decision
        action = self._decide_optimization(analysis, issues)
        
        # Apply optimization if needed
        if action['optimize']:
            success = self._apply_optimization(action)
            if success:
                self._log_optimization(analysis, action)
                logger.info(f"✓ Self-healing applied: {action['description']}")
            else:
                logger.error("Failed to apply optimization")
                action['applied'] = False
        
        return {
            "status": "analyzed",
            "issues_found": issues,
            "analysis": analysis,
            "action": action
        }
    
    def _get_todays_logs(self) -> str:
        """Get today's log file content."""
        try:
            # Find today's log file
            log_dir = Path(__file__).parent.parent.parent / "ai_trader_data" / "logs" / "2026"
            today = datetime.now().strftime("%Y%m%d")
            
            log_files = list(log_dir.glob(f"*{today}*.log"))
            if not log_files:
                logger.warning(f"No log files found for {today}")
                return ""
            
            # Read most recent log file
            log_file = sorted(log_files)[-1]
            logger.info(f"Analyzing log file: {log_file.name}")
            
            with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read()
                
        except Exception as e:
            logger.error(f"Error reading log files: {e}")
            return ""
    
    def _extract_error_patterns(self, log_content: str) -> Dict:
        """Extract error patterns from logs using regex."""
        issues = {
            'has_issues': False,
            'oom_errors': 0,
            'crash_recoveries': 0,
            'worker_failures': 0,
            'health_check_failures': 0,
            'examples': []
        }
        
        # Pattern: Out of Memory
        oom_pattern = r'(out of memory|OOM|CUDA.*memory|memory.*full)'
        oom_matches = re.findall(oom_pattern, log_content, re.IGNORECASE)
        issues['oom_errors'] = len(oom_matches)
        
        # Pattern: Worker pool recovery attempts
        recovery_pattern = r'(Worker pool recovery|attempting recovery|recovery failed)'
        recovery_matches = re.findall(recovery_pattern, log_content, re.IGNORECASE)
        issues['crash_recoveries'] = len(recovery_matches)
        
        # Pattern: Worker failures
        worker_failure_pattern = r'(Worker \d+ failed|Worker.*error|Worker.*timeout)'
        worker_matches = re.findall(worker_failure_pattern, log_content, re.IGNORECASE)
        issues['worker_failures'] = len(worker_matches)
        
        # Pattern: Health check failures
        health_pattern = r'(health check failed|Ollama.*unhealthy|connection lost)'
        health_matches = re.findall(health_pattern, log_content, re.IGNORECASE)
        issues['health_check_failures'] = len(health_matches)
        
        # Extract example error lines for context
        error_lines = [
            line for line in log_content.split('\n')
            if any(keyword in line.lower() for keyword in ['error', 'failed', 'oom', 'crash'])
        ]
        issues['examples'] = error_lines[-10:]  # Last 10 error lines
        
        # Determine if issues are significant
        issues['has_issues'] = (
            issues['oom_errors'] > 0 or
            issues['crash_recoveries'] > 2 or
            issues['worker_failures'] > 5 or
            issues['health_check_failures'] > 3
        )
        
        return issues
    
    def _analyze_with_ollama(self, log_content: str, issues: Dict) -> Optional[Dict]:
        """Use Ollama to analyze log patterns and recommend action."""
        if not self.ollama or not self.ollama.is_available():
            return None
        
        # Truncate log content to avoid overwhelming the model
        log_excerpt = "\n".join(issues['examples']) if issues['examples'] else log_content[-5000:]
        
        prompt = f"""Analyze these error patterns from today's trading system logs:

Error Summary:
- OOM errors: {issues['oom_errors']}
- Crash recoveries: {issues['crash_recoveries']}
- Worker failures: {issues['worker_failures']}
- Health check failures: {issues['health_check_failures']}

Recent Error Examples:
{log_excerpt}

Current Configuration:
- Worker pool: 3 workers using qwen2.5:7b
- GPU: RTX 3090 (24GB VRAM)

Determine:
1. Severity (low/medium/high)
2. Root cause (OOM, network, model issues, etc.)
3. Recommendation (keep 3 workers, reduce to 2, disable pool, etc.)
4. Confidence (0-100%)

Respond in JSON:
{{
  "severity": "low/medium/high",
  "root_cause": "description",
  "recommendation": "keep_3|reduce_to_2|disable",
  "confidence": 85,
  "reasoning": "explanation"
}}"""

        system = (
            "You are a system reliability engineer analyzing GPU worker pool issues. "
            "Focus on patterns that indicate VRAM constraints, model loading failures, "
            "or persistent crashes. Be conservative - only recommend changes if clear patterns exist."
        )
        
        try:
            result = self.ollama.complete_json(prompt, system=system)
            if result:
                logger.info(f"Ollama analysis: {result.get('severity')} severity, {result.get('recommendation')}")
                return result
        except Exception as e:
            logger.error(f"Ollama analysis failed: {e}")
        
        return None
    
    def _fallback_analysis(self, issues: Dict) -> Dict:
        """Fallback analysis if Ollama unavailable."""
        if issues['oom_errors'] > 0:
            return {
                "severity": "high",
                "root_cause": "Out of memory errors detected",
                "recommendation": "reduce_to_2",
                "confidence": 90,
                "reasoning": "OOM errors indicate VRAM constraints"
            }
        elif issues['crash_recoveries'] > 5:
            return {
                "severity": "medium",
                "root_cause": "Frequent crash recoveries",
                "recommendation": "reduce_to_2",
                "confidence": 75,
                "reasoning": "Multiple crash recoveries suggest instability"
            }
        elif issues['worker_failures'] > 10:
            return {
                "severity": "medium",
                "root_cause": "High worker failure rate",
                "recommendation": "reduce_to_2",
                "confidence": 70,
                "reasoning": "Excessive worker failures"
            }
        else:
            return {
                "severity": "low",
                "root_cause": "Minor issues detected",
                "recommendation": "keep_3",
                "confidence": 60,
                "reasoning": "Issues are within acceptable range"
            }
    
    def _decide_optimization(self, analysis: Dict, issues: Dict) -> Dict:
        """Decide whether to optimize based on analysis."""
        recommendation = analysis.get('recommendation', 'keep_3')
        confidence = analysis.get('confidence', 0)
        
        # Only optimize if high confidence and clear recommendation
        if recommendation == 'reduce_to_2' and confidence >= 70:
            return {
                "optimize": True,
                "new_worker_count": 2,
                "description": "Reduce from 3 to 2 workers due to stability issues",
                "reasoning": analysis.get('reasoning', 'Pattern-based decision'),
                "confidence": confidence
            }
        elif recommendation == 'disable' and confidence >= 85:
            return {
                "optimize": True,
                "new_worker_count": 0,
                "description": "Disable worker pool due to critical issues",
                "reasoning": analysis.get('reasoning', 'Critical issues detected'),
                "confidence": confidence
            }
        else:
            return {
                "optimize": False,
                "new_worker_count": 3,
                "description": "No optimization needed",
                "reasoning": "Issues are within acceptable limits or confidence too low",
                "confidence": confidence
            }
    
    def _apply_optimization(self, action: Dict) -> bool:
        """Apply the optimization to broker_config.json."""
        try:
            # Read current config
            with open(self.config_path, 'r') as f:
                config = json.load(f)
            
            # Store previous value
            old_value = config.get('ollama_worker_pool', {}).get('num_workers', 3)
            
            # Update worker count
            if 'ollama_worker_pool' not in config:
                config['ollama_worker_pool'] = {}
            
            if action['new_worker_count'] == 0:
                config['ollama_worker_pool']['enabled'] = False
            else:
                config['ollama_worker_pool']['num_workers'] = action['new_worker_count']
                config['ollama_worker_pool']['enabled'] = True
            
            # Add optimization note
            config['ollama_worker_pool']['last_optimization'] = {
                'timestamp': datetime.now().isoformat(),
                'old_value': old_value,
                'new_value': action['new_worker_count'],
                'reason': action['reasoning'],
                'confidence': action['confidence']
            }
            
            # Write back to file
            with open(self.config_path, 'w') as f:
                json.dump(config, f, indent=2)
            
            logger.info(f"✓ Config updated: num_workers {old_value} → {action['new_worker_count']}")
            action['applied'] = True
            action['old_value'] = old_value
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to apply optimization: {e}")
            return False
    
    def _log_optimization(self, analysis: Dict, action: Dict):
        """Log optimization to history file."""
        try:
            # Load existing log
            if self.optimization_log_path.exists():
                with open(self.optimization_log_path, 'r') as f:
                    log_data = json.load(f)
            else:
                log_data = {"optimizations": []}
            
            # Add new entry
            log_data['optimizations'].append({
                "timestamp": datetime.now().isoformat(),
                "analysis": analysis,
                "action": action
            })
            
            # Keep last 30 days only
            if len(log_data['optimizations']) > 30:
                log_data['optimizations'] = log_data['optimizations'][-30:]
            
            # Write back
            self.optimization_log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.optimization_log_path, 'w') as f:
                json.dump(log_data, f, indent=2)
            
            logger.info(f"✓ Optimization logged to {self.optimization_log_path.name}")
            
        except Exception as e:
            logger.error(f"Failed to log optimization: {e}")
    
    def get_optimization_history(self) -> List[Dict]:
        """Get history of optimizations."""
        try:
            if self.optimization_log_path.exists():
                with open(self.optimization_log_path, 'r') as f:
                    data = json.load(f)
                    return data.get('optimizations', [])
        except Exception as e:
            logger.error(f"Failed to read optimization history: {e}")
        
        return []


def run_daily_optimization(ollama_provider) -> Dict:
    """
    Run daily worker pool optimization.
    
    Called by automated_scheduler at end of day (4:00 PM).
    
    Args:
        ollama_provider: OllamaProvider instance
        
    Returns:
        Dict with optimization results
    """
    logger.info("=" * 70)
    logger.info("WORKER POOL SELF-HEALING ANALYSIS")
    logger.info("=" * 70)
    
    optimizer = WorkerPoolOptimizer(ollama_provider)
    result = optimizer.analyze_and_optimize()
    
    if result.get('action', {}).get('optimize'):
        logger.info(f"🔧 Self-healing applied: {result['action']['description']}")
        logger.info(f"   Confidence: {result['action']['confidence']}%")
        logger.info(f"   Reasoning: {result['action']['reasoning']}")
    else:
        logger.info("✓ No optimization needed - worker pool healthy")
    
    logger.info("=" * 70)
    
    return result