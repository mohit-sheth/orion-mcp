"""
Utility functions for the orion-mcp server.

This module contains helper functions for running Orion analysis,
converting data formats, and generating visualizations.
"""

import base64
import io
import os
import subprocess
import json
import shutil
import asyncio
import matplotlib.pyplot as plt
import numpy as np
from typing import Optional


async def run_command_async(command: list[str] | str, env: Optional[dict] = None, shell: bool = False) -> subprocess.CompletedProcess:
    """
    Asynchronously executes a command-line tool and returns a CompletedProcess-like result.
    
    Parameters:
        command (list[str] | str): The command to execute, as a list of arguments or a string if shell is True.
        env (Optional[dict]): Optional environment variables to set for the command.
        shell (bool): Whether to execute the command through the shell.
    
    Returns:
        subprocess.CompletedProcess: An object containing the command arguments, return code, standard output, and standard error.
    """
    print(f"Running command: {command}")
    if env is not None:
        env_vars = os.environ.copy()
        env_vars.update(env)
    else:
        env_vars = None
    try:
        if shell:
            if not isinstance(command, str):
                raise TypeError("command must be a string when shell=True")
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env_vars
            )
        else:
            if not isinstance(command, list):
                raise TypeError("command must be a list when shell=False")
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env_vars
            )
        stdout, stderr = await process.communicate()
        result = subprocess.CompletedProcess(
            args=command,
            returncode=process.returncode,
            stdout=stdout.decode('utf-8'),
            stderr=stderr.decode('utf-8')
        )
        return result
    except (OSError, subprocess.SubprocessError) as e:
        return subprocess.CompletedProcess(
            args=command,
            returncode=1,
            stdout='',
            stderr=str(e)
        )

def orion_configs(configs: list[str]) -> list[str]:
    """
    Return the list of Orion config filenames (not full paths).
    """
    return [os.path.basename(cfg) for cfg in configs]

async def run_orion(
    lookback: str,
    config: str,
    data_source: str,
    version: str,
) -> subprocess.CompletedProcess:
    """
    Runs the Orion tool to analyze performance regressions using the specified configuration, data source, and version.
    
    Parameters:
        lookback (str): Number of days to look back for performance data (e.g., "10").
        config (str): Path to the Orion configuration file.
        data_source (str): OpenSearch URL containing the data to analyze.
        version (str): Version identifier for the analysis.
    
    Returns:
        subprocess.CompletedProcess: The result of the Orion command execution, including stdout and stderr.
    
    Raises:
        ValueError: If the data source is not set.
    """
    if data_source == "":
        raise ValueError("Data source is not set")
    command = []
    if not shutil.which("orion"):
        print("Using orion from podman")
        command = [
            "podman",
            "run",
            "--env-host",
            "orion",
            "orion",
            "cmd",
            "--lookback", f"{lookback}d",
            "--hunter-analyze",
            "--config", config,
            "-o", "json"
        ]
    else:
        print("Using orion from path")
        command = [
            "orion",
            "cmd",
            "--lookback", f"{lookback}d",
            "--hunter-analyze",
            "--config", config,
            "-o", "json"
        ]
    env = {
        "ES_SERVER": data_source,
        "version": version,
        "es_metadata_index": "perf_scale_ci*",
        "es_benchmark_index": "ripsaw-kube-burner-*"
    }
    result = await run_command_async(command, env=env)
    # Log the full result for debugging
    print(f"Orion return code: {result.returncode}")
    print(f"Orion stdout: {result.stdout}")
    print(f"Orion stderr: {result.stderr}")
    return result


async def summarize_result(result: subprocess.CompletedProcess, isolate: Optional[str] = None) -> dict | str:
    """
    Parses and summarizes Orion JSON analysis results from a CompletedProcess object.
    
    If an `isolate` metric name is provided, only that metric is included in the summary. Returns a dictionary mapping metric names to their values and includes the timestamp of each run. If parsing fails, returns an error string.
    """
    summary = {}
    try:
        data = json.loads(result.stdout)
        if len(data) == 0:
            return {}
        for run in data:
            for metric_name, metric_data in run["metrics"].items():
                summary["timestamp"] = run["timestamp"]
                # Isolate specific metric if specified
                if isolate is not None:
                    if isolate != metric_name:
                        print(f"Skipping {metric_name} because it doesn't contain {isolate}")
                        continue
                if metric_name not in summary:
                    summary[metric_name] = {}
                    summary[metric_name] = {
                        "value": [metric_data["value"]],
                    }
                else:
                    summary[metric_name]["value"].append(metric_data["value"])
    except Exception as e:
        return f"Error : {e}"
    print(summary)
    return summary


def get_data_source() -> str:
    """
    Returns the OpenSearch server URL from the ES_SERVER environment variable.
    
    Raises:
        EnvironmentError: If the ES_SERVER environment variable is not set.
    """
    value = os.environ.get("ES_SERVER")
    if value is None:
        raise EnvironmentError("ES_SERVER environment variable is not set")
    return value


async def orion_metrics(config_list: list) -> dict | str:
    """
    Retrieves available metric names for each Orion configuration file.
    
    For each config file in the input list, runs Orion analysis and summarizes the result to extract metric names. Returns a dictionary mapping each config file to a list of its available metric names. If an error occurs during processing, returns an error string.
    """
    metrics = {} 
    for config in config_list:
        metrics[config] = []
        result = await run_orion(
            lookback="10",
            config=config,
            data_source=get_data_source(),
            version="4.19"
        )
        try:
            sum_result = await summarize_result(result)
            print(f"Sum result: {sum_result}")
            if isinstance(sum_result, dict):
                metrics[config].append(list(sum_result.keys()))
            else:
                return f"Error processing result for {config}: {sum_result}"
        except (KeyError, ValueError, TypeError) as e:
            return f"Error processing result for {config}: {e}"

    return metrics 
    

# Correlation helper functions

def compute_correlation(values1: list[float], values2: list[float]) -> float:
    """
    Compute the Pearson correlation coefficient between two numeric lists.

    Args:
        values1: First list of numeric values.
        values2: Second list of numeric values.

    Returns:
        The Pearson correlation coefficient. Returns ``float('nan')`` if the
        lists are of unequal length or contain fewer than two items.
    """
    if len(values1) != len(values2) or len(values1) < 2:
        return float("nan")

    # Use NumPy for a robust computation
    return float(np.corrcoef(values1, values2)[0, 1])


def generate_correlation_plot(
    values1: list[float],
    values2: list[float],
    metric1: str,
    metric2: str,
    title_prefix: str = "",
) -> bytes:
    """
    Generates a scatter plot visualizing the correlation between two metric value lists and returns the plot as base64-encoded PNG bytes.
    
    Parameters:
        values1 (list[float]): Values for the first metric, plotted on the Y-axis.
        values2 (list[float]): Values for the second metric, plotted on the X-axis.
        metric1 (str): Name of the first metric.
        metric2 (str): Name of the second metric.
        title_prefix (str, optional): String to prepend to the plot title.
    
    Returns:
        bytes: Base64-encoded PNG image of the correlation scatter plot.
    """

    corr = compute_correlation(values1, values2)

    plt.figure(figsize=(8, 6))
    plt.scatter(values2, values1, alpha=0.7, color="steelblue")
    plt.title(f"{title_prefix}Correlation between {metric1} and {metric2}\nPearson r = {corr:.3f}")
    plt.xlabel(metric2)
    plt.ylabel(metric1)
    plt.grid(True, linestyle="--", alpha=0.5)
    plt.tight_layout()

    buffer = io.BytesIO()
    plt.savefig(buffer, format="png")
    plt.close()
    buffer.seek(0)
    return base64.b64encode(buffer.read())
 
# Multi-line plot helper
def generate_multi_line_plot(
    series_dict: dict[str, list[float]],
    metric: str,
    title_prefix: str = "",
) -> bytes:
    """
    Generates a multi-line plot from a dictionary of labeled numeric series and returns the plot as base64-encoded PNG bytes.
    
    Parameters:
        series_dict (dict[str, list[float]]): Dictionary mapping labels to lists of numeric values, each plotted as a separate line.
        metric (str): Name of the metric for the y-axis label and plot title.
        title_prefix (str, optional): String to prepend to the plot title.
    
    Returns:
        bytes: Base64-encoded PNG image of the generated plot.
    
    Raises:
        ValueError: If the input dictionary is empty.
    """

    if not series_dict:
        raise ValueError("series_dict is empty—nothing to plot")

    plt.figure(figsize=(12, 7))
    cmap = plt.cm.get_cmap("tab10")
    color_cycle = [cmap(i) for i in range(cmap.N)]  # list of RGBA tuples

    for idx, (label, values) in enumerate(series_dict.items()):
        if not values:
            continue
        plt.plot(
            range(len(values)),
            values,
            marker="o",
            linestyle="-",
            color=color_cycle[idx % len(color_cycle)],
            label=label,
        )

    plt.title(f"{title_prefix}{metric} over time", fontsize=16)
    plt.xlabel("Measurement Index", fontsize=14)
    plt.ylabel(metric, fontsize=14)
    plt.grid(True, linestyle="--", alpha=0.7)
    plt.legend()
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format="png")
    plt.close()
    buf.seek(0)
    return base64.b64encode(buf.read())
    
