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


async def run_command_async(command: list[str], env: dict = None) -> subprocess.CompletedProcess:
    """
    Run a command line tool asynchronously and return a CompletedProcess-like result.
    Args:
        command: List of command arguments.
        env: Optional environment variables to set for the command.
    Returns:
        A subprocess.CompletedProcess-like object with args, returncode, stdout, stderr.
    """
    if env is not None:
        env_vars = os.environ.copy()
        env_vars.update(env)
    else:
        env_vars = None
    try:
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
    except Exception as e:
        return subprocess.CompletedProcess(
            args=command,
            returncode=1,
            stdout='',
            stderr=str(e)
        )


async def run_orion(
    lookback: str,
    config: str,
    data_source: str,
    version: str,
) -> subprocess.CompletedProcess:
    """
    Execute Orion to analyze performance data for regressions.

    Args:
        lookback: Days to look back for performance data.
        config: Path to the Orion configuration file to use for analysis.
        data_source: Location of the data (OpenSearch URL) to analyze.
        version: Version to analyze.

    Returns:
        The result of the Orion command execution, including stdout and stderr.
    """
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
    return result


def convert_results_to_csv(results: list) -> str:
    """
    Convert the results of the Orion analysis into a CSV format.

    Args:
        results: The list of results from the Orion analysis.

    Returns:
        A string containing the CSV representation of the results.
    """
    csv_lines = []
    for result in results:
        for config, data in result.items():
            if not data:
                continue
            for metric, values in data.items():
                if metric == "timestamp":
                    continue
                csv_lines.append(f"{config},{metric},{','.join(map(str, values['value']))}")
    return "\n".join(csv_lines)


async def csv_to_graph(csv_data: str) -> list[bytes]:
    """
    Convert CSV data into a graph representation.

    Args:
        csv_data: The CSV data to convert.

    Returns:
        List of base64 encoded graph images.
    """

    # Dictionary to store parsed data:
    # Key: (file_path, metric_name) tuple
    # Value: List of numerical data points
    parsed_metrics_data = {}

    # Group the data by workload and metric
    grouped_data = {}
    for line in csv_data.strip().split('\n'):
        if not line.strip():
            continue
        parts = line.strip().split(',')
        if len(parts) < 3:
            continue
        file_path = parts[0]
        metric_name = parts[1]
        value = parts[2:]

        if file_path not in grouped_data:
            grouped_data[file_path] = {}
        if metric_name not in grouped_data[file_path]:
            grouped_data[file_path][metric_name] = value

    # Split the input string into individual lines
    lines = csv_data.strip().split('\n')

    for line_num, line in enumerate(lines):
        if not line.strip():  # Skip empty lines
            continue

        parts = line.strip().split(',')
        if len(parts) < 3:  # Ensure at least path, metric_name, and one value
            print(f"Warning: Skipping malformed line {line_num + 1}: '{line}'. "
                  f"Expected at least 3 comma-separated parts.")
            continue

        file_path = parts[0]
        metric_name = parts[1]
        raw_values = parts[2:]

        # Convert raw_values to floats, handling 'None' values
        numerical_values = []
        for val_str in raw_values:
            try:
                # Convert 'None' string to actual None, then filter out
                numerical_values.append(float(val_str) if val_str.lower() != 'none' else None)
            except ValueError:
                print(f"Warning: Could not convert '{val_str}' to a number in line {line_num + 1}. "
                      f"Skipping this value.")
                numerical_values.append(None)

        # Filter out actual None values from the list before storing
        numerical_values = [val for val in numerical_values if val is not None]

        if not numerical_values:
            print(f"Warning: No valid numerical data found for metric '{metric_name}' "
                  f"in '{file_path}'. Skipping plot for this entry.")
            continue

        # Store the data using a tuple as key for unique identification
        key = (file_path, metric_name)
        parsed_metrics_data[key] = numerical_values

    if not parsed_metrics_data:
        print("No valid metric data found to generate graphs.")
        return []

    imgs = []

    # Generate a line graph for each metric entry
    for (file_path, metric_name), values in parsed_metrics_data.items():
        plt.figure(figsize=(12, 7))  # Set a good figure size
        # The X-axis will just be the index of the measurement
        plt.plot(range(len(values)), values, marker='o', linestyle='-', color='skyblue', linewidth=2)

        # Sanitize file_path for use in filename (replace slashes and dots with underscores)
        # and limit length to avoid overly long filenames
        sanitized_file_path = file_path.replace('/', '_').replace('.', '_').strip('_')
        if len(sanitized_file_path) > 50:  # Limit length
            sanitized_file_path = sanitized_file_path[-50:]  # Take last 50 chars

        # Add labels and title
        plt.title(f'{metric_name} Values\n(Workload: {file_path})', fontsize=16)
        plt.xlabel('Measurement Index', fontsize=12)
        plt.ylabel(f'{metric_name} Value', fontsize=12)
        plt.grid(True, linestyle='--', alpha=0.7)
        plt.tick_params(axis='x', labelsize=10)
        plt.tick_params(axis='y', labelsize=10)

        # Add a tight layout to prevent labels from overlapping
        plt.tight_layout()

        # Save the plot
        try:
            img = io.BytesIO()
            plt.savefig(img, format='png')
            img.seek(0)
            img_data = base64.b64encode(img.read())
            imgs.append(img_data)
        except Exception as e:
            print(f"Error with graph : {e}")
        plt.close()  # Close the figure to free up memory

    # Add a small delay to prevent blocking
    await asyncio.sleep(0.01)
    return imgs


async def summarize_result(result: subprocess.CompletedProcess) -> dict:
    """
    Summarize the Orion result into a dictionary.

    Args:
        result: The json output from the Orion command.

    Returns:
        A dictionary containing the summary of the Orion analysis.
    """
    summary = {}
    try:
        data = json.loads(result.stdout)
        if len(data) == 0:
            return {}
        for run in data:
            for metric_name, metric_data in run["metrics"].items():
                summary["timestamp"] = run["timestamp"]
                if metric_name not in summary:
                    summary[metric_name] = {}
                    summary[metric_name] = {
                        "value": [metric_data["value"]],
                    }
                else:
                    summary[metric_name]["value"].append(metric_data["value"])
    except Exception as e:
        return f"Error : {e}"
    return summary


def get_data_source() -> str:
    """
    Provide the data source URL for Orion analysis.

    User must launch MCP server with the environment variable ES_SERVER
    set to the OpenSearch URL.

    Returns:
        The OpenSearch URL as a string.
    """
    return os.environ.get("ES_SERVER") 

async def get_orion_metrics(orion_configs: list) -> str:
    """
    Provide the metrics for Orion analysis.
    """
    config_list = " ".join(orion_configs)
    command = [
        "grep",
        "metricName",
        config_list,
        "|",
        "awk",
        "'{print $4}'", 
        "|",
        "sort",
        "|",
        "uniq",
        "|",
        "grep",
        "-v",
        "'^$'",
    ]
    result = await run_command_async(command)
    return result.stdout

