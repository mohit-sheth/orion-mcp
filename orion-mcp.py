# orion-mcp.py
# A Model Context Protocol (MCP) server that provides a tool for running
# performance regression analysis using the cloud-bulldozer/orion library.

import base64
import io
import os
import subprocess
import json
import shutil
from typing import Annotated
from pydantic import Field

import pandas as pd
import matplotlib.pyplot as plt

import mcp.types as types
from typing import Literal, Optional
from mcp.server.fastmcp import FastMCP, Context

mcp = FastMCP(name="orion-mcp",
              host="0.0.0.0",
              port=3030,
              log_level='INFO')

def run_orion(
    lookback: str,
    config : str,
    data_source: str,
    version: str,
) -> subprocess.CompletedProcess :
    """
    Executes Orion to analyze performance data for regressions.

    Args:
        lookback (str): Days to look back for performance data. 
        config (str): Path to the Orion configuration file to use for analysis.
        data_source (str):  Location of the data (OpenSearch URL) to analyze. 
        version (str): Version to analyze. 

    Returns:
        subprocess.CompletedProcess: The result of the Orion command execution, including stdout and stderr. 
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
            "--lookback","{}d".format(lookback),
            "--hunter-analyze",
            "--config", config,
            "-o","json"
        ]
    else:
        print("Using orion from path")
        command = [
            "orion",
            "cmd",
            "--lookback","{}d".format(lookback),
            "--hunter-analyze",
            "--config", config,
            "-o","json"
        ]

    os.environ["ES_SERVER"] = data_source
    os.environ["version"] = version
    os.environ["es_metadata_index"] = "perf_scale_ci*"
    os.environ["es_benchmark_index"] = "ripsaw-kube-burner-*"
    try:
        # Execute the command as a subprocess
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False
        )
            
        if result.returncode != 0:
            return result 

    except FileNotFoundError:
        err_msg = f"Error: 'orion' command not found. Please ensure the cloud-bulldozer/orion tool is installed and in your PATH. COMMAND: {' '.join(command)}"
        return err_msg
    except subprocess.CalledProcessError as e:
        error_message = f"Orion analysis failed with exit code {e.returncode}.\n"
        error_message += f"Stderr:\n{e.stderr}"
        error_message += f"Command: {' '.join(command)}\n"
        return error_message
    except Exception as e:
        # Catch any other unexpected errors.
        return f"An unexpected error occurred: {str(e)} \nCommand: {' '.join(command)}"
    return result

def convert_results_to_csv(results : list) -> str:
    """
    Converts the results of the Orion analysis into a CSV format.

    Args:
        results (list): The list of results from the Orion analysis.

    Returns:
        str: A string containing the CSV representation of the results.
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

def csv_to_graph(
    csv_data: str,
) -> list[bytes]:
    """
    Converts CSV data into a graph representation.

    Args:
        csv_data (str): The CSV data to convert.

    Returns:
       Decoded base64 string of the generated graph image. 
    """

    # Dictionary to store parsed data:
    # Key: (file_path, metric_name) tuple
    # Value: List of numerical data points
    parsed_metrics_data = {}

    # Split the input string into individual lines
    lines = csv_data.strip().split('\n')

    for line_num, line in enumerate(lines):
        if not line.strip(): # Skip empty lines
            continue

        parts = line.strip().split(',')
        if len(parts) < 3: # Ensure at least path, metric_name, and one value
            print(f"Warning: Skipping malformed line {line_num + 1}: '{line}'. Expected at least 3 comma-separated parts.")
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
                print(f"Warning: Could not convert '{val_str}' to a number in line {line_num + 1}. Skipping this value.")
                numerical_values.append(None)

        # Filter out actual None values from the list before storing
        numerical_values = [val for val in numerical_values if val is not None]

        if not numerical_values:
            print(f"Warning: No valid numerical data found for metric '{metric_name}' in '{file_path}'. Skipping plot for this entry.")
            continue

        # Store the data using a tuple as key for unique identification
        key = (file_path, metric_name)
        parsed_metrics_data[key] = numerical_values

    if not parsed_metrics_data:
        print("No valid metric data found to generate graphs.")
        return

    imgs = []

    # Generate a line graph for each metric entry
    for (file_path, metric_name), values in parsed_metrics_data.items():
        plt.figure(figsize=(12, 7)) # Set a good figure size
        # The X-axis will just be the index of the measurement
        plt.plot(range(len(values)), values, marker='o', linestyle='-', color='skyblue', linewidth=2)

        # Sanitize file_path for use in filename (replace slashes and dots with underscores)
        # and limit length to avoid overly long filenames
        sanitized_file_path = file_path.replace('/', '_').replace('.', '_').strip('_')
        if len(sanitized_file_path) > 50: # Limit length
            sanitized_file_path = sanitized_file_path[-50:] # Take last 50 chars

        # Add labels and title
        plt.title(f'{metric_name} Values\n(Source: {file_path})', fontsize=16)
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
        plt.close() # Close the figure to free up memory
    return imgs

def summarize_result(
    result: subprocess.CalledProcessError 
) -> dict :
    """
    summarizes the orion result into a dictionary.

    args:
        result (str): the json output from the orion command.
        config (str): the configuration file used for the orion analysis.

    returns:
        dict: a dictionary containing the summary of the orion analysis.
    """
    summary = {}
    try:
        data = json.loads(result.stdout)
        if len(data) == 0:
            return {}
        for run in data :
            for metric_name, metric_data in run["metrics"].items():
                summary["timestamp"] = run["timestamp"]
                if metric_name not in summary:
                    summary[metric_name] = {}
                    summary[metric_name] = {
                        "value": [ metric_data["value"] ],
                    }
                else :
                    summary[metric_name]["value"].append(metric_data["value"]) 
    except Exception as e:
        return f"Error : {e}" 
    return summary

@mcp.resource("orion-mcp://get_data_source")
def get_data_source()->str:
    """
    provides the data source url for orion analysis. user must launch mcp 
    server with the environment variable es_server set to the opensearch url.

    args:
        data_source: The OpenSearch URL where performance data is stored.

    Returns:
        The OpenSearch URL as a string.
    """
    return os.environ.get("ES_SERVER") 

@mcp.tool()
def openshift_detailed_performance(
    version: Annotated[str, Field(description="Version of OpenShift to look into")] = "4.19",
    lookback: Annotated[str, Field(description="Number of days to lookback")] = "15",
) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    """
    Captures a performance analysis against the specified OpenShift version using Orion 
    and provides visual report.

    Orion uses an EDivisive algorithm to analyze performance data from a specified
    configuration file to detect any performance regressions.

    Args:
        version: Openshift version to look into.
        lookback: The number of days to look back for performance data. Defaults to 15 days.

    Returns:
        Returns an image or a set of images showing the performance overtime.
    """
    orion_configs = ["/orion/examples/trt-external-payload-cluster-density.yaml",
                     "/orion/examples/trt-external-payload-node-density.yaml",
                     "/orion/examples/trt-external-payload-node-density-cni.yaml",
                     "/orion/examples/trt-external-payload-crd-scale.yaml"]

    # Store all the results in a list
    results = [] 
    # Prepare the command to run the orion tool.
    for config in orion_configs:
        data = {}
        result = run_orion(
            lookback=lookback,
            config=config,
            data_source=get_data_source(),
            version=version
        )
        if result.returncode != 0:
            return csv_to_graph(convert_results_to_csv(summarize_result([result])))

        data[config] = {}
        data[config] = summarize_result(result)
        results.append(data)
    b64_imgs = csv_to_graph(convert_results_to_csv(results))
    imgs = []
    for img in b64_imgs:
        if img is None:
            continue
        b64_img = img.decode('utf-8')
        # Create an image content type for each graph
        imgs.append(
            types.ImageContent(
                type="image", data=b64_img, mimeType="image/jpeg"
            ))
    return imgs

@mcp.tool()
def has_openshift_regressed(
    version: Annotated[str, Field(description="Version of OpenShift to look into")] = "4.19",
    lookback: Annotated[str, Field(description="Number of days to lookback")] = "15",
) -> bool:
    """
    Runs a performance regression analysis against the OpenShift version using Orion and provides a high-level pass or fail.

    Orion uses an EDivisive algorithm to analyze performance data from a specified
    configuration file to detect any performance regressions.

    Args:
        version: Openshift version to look into.
        lookback: The number of days to look back for performance data. Defaults to 15 days.

    Returns:
        Returns true if there is a regression and false if there is no regression found.
    """

    orion_configs = ["/orion/examples/trt-external-payload-cluster-density.yaml",
                     "/orion/examples/trt-external-payload-node-density.yaml",
                     "/orion/examples/trt-external-payload-node-density-cni.yaml",
                     "/orion/examples/trt-external-payload-crd-scale.yaml"]

    for config in orion_configs:
        # Execute the command as a subprocess
        result = run_orion(
            lookback=lookback,
            config=config,
            data_source=get_data_source(),
            version=version
        )
        if result.returncode != 0:
            return True
    return False

def main():
    """
    Main function to run the MCP server.
    """
if __name__ == "__main__":
    transport="sse"
    mcp.run(transport=transport)
    print("Running MCP server with transport:", transport)