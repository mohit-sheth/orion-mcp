"""
Model Context Protocol (MCP) server for Orion performance regression analysis.

This module provides tools for running performance regression analysis using
the cloud-bulldozer/orion library.
"""

import asyncio
import json
import os
from typing import Annotated
from pydantic import Field

from mcp import types
from mcp.server.fastmcp import FastMCP

# Import utility functions from utils module
from utils.utils import (
    run_orion,
    summarize_result,
    get_data_source,
    orion_metrics,
    orion_configs,
    generate_correlation_plot,
    generate_multi_line_plot
)

mcp = FastMCP(name="orion-mcp",
              host="0.0.0.0",
              port=3030,
              log_level='INFO')

ORION_CONFIGS = [
    "/orion/examples/metal-perfscale-cpt-virt-udn-density.yaml",
    "/orion/examples/trt-external-payload-cluster-density.yaml",
    "/orion/examples/trt-external-payload-node-density.yaml",
    "/orion/examples/trt-external-payload-node-density-cni.yaml",
    "/orion/examples/trt-external-payload-crd-scale.yaml",
    "/orion/examples/small-scale-udn-l3.yaml",
    "/orion/examples/med-scale-udn-l3.yaml",
]
ORION_CONFIGS_PATH = "/orion/examples/"

@mcp.resource("orion-mcp://get_data_source")
def get_data_source_resource() -> str:
    """
    Return the OpenSearch URL used as the data source for Orion analysis.
    
    Requires the MCP server to be started with the ES_SERVER environment variable set to the OpenSearch URL.
    
    Returns:
        str: The OpenSearch URL.
    """
    return get_data_source()

@mcp.tool()
def get_orion_configs() -> list[str]:
    """
    Return a list of available Orion configuration filenames.
    
    Returns:
        list[str]: Filenames of Orion configuration files without directory paths.
    """
    return orion_configs(ORION_CONFIGS)

@mcp.tool()
async def get_orion_metrics(
    config: Annotated[str, Field(description="Orion configuration file name (e.g. 'small-scale-udn-l3.yaml')")] = "small-scale-udn-l3.yaml",
) -> dict:
    """
    Retrieve the available metrics for a specified Orion configuration file.
    
    Parameters:
        config (str): The filename of the Orion configuration to query (e.g., 'small-scale-udn-l3.yaml').
    
    Returns:
        dict: A dictionary mapping the full configuration file path to a list of available metric names, or an error message if retrieval fails.
    """

    # Query only the requested config
    result = await orion_metrics([ORION_CONFIGS_PATH + config])

    if isinstance(result, str):
        return {"error": f"Failed to fetch Orion metrics: {result}"}

    return result


@mcp.tool()
async def openshift_report_on(
    versions: Annotated[str, Field(description="Comma-separated list of OpenShift versions e.g. '4.19,4.20'")] = "4.19",
    lookback: Annotated[str, Field(description="Number of days to lookback")] = "15",
    metric: Annotated[str, Field(description="Metric to analyze")] = "podReadyLatency_P99",
    config: Annotated[str, Field(description="Config to analyze")] = "small-scale-udn-l3.yaml",
) -> types.ImageContent | types.TextContent:
    """
    Performs performance analysis for multiple OpenShift versions using Orion and visualizes metric trends.
    
    For each specified OpenShift version, runs Orion analysis with the given configuration and lookback period, extracts the specified metric, and aggregates results into a time series. Generates a multi-line plot (as a base64-encoded JPEG image) showing the metric's values over time for each version. Returns a textual error message if data is missing or malformed.
    
    Parameters:
        versions (str): Comma-separated list of OpenShift versions to analyze (e.g., "4.19,4.20").
        lookback (str): Number of days to look back for performance data.
        metric (str): Name of the metric to analyze.
        config (str): Orion configuration file name.
    
    Returns:
        types.ImageContent: Image of the multi-line plot showing metric trends per version.
        types.TextContent: Textual error message if analysis or data extraction fails.
    """

    # Parse versions into list
    if isinstance(versions, str):
        version_list = [v.strip() for v in versions.split(',') if v.strip()]
    else:
        version_list = list(versions)

    series: dict[str, list[float]] = {}

    for ver in version_list:
        result = await run_orion(
            lookback=lookback,
            config=ORION_CONFIGS_PATH + config,
            data_source=get_data_source(),
            version=ver,
        )

        sum_result = await summarize_result(result, isolate=metric)

        # Ensure we have the expected structure before indexing
        if not isinstance(sum_result, dict) or metric not in sum_result:
            return types.TextContent(type="text", text=f"No data for version {ver}: {sum_result}")

        raw_values = sum_result[metric].get("value", [])  # type: ignore[assignment]
        if not isinstance(raw_values, list):
            return types.TextContent(type="text", text=f"Unexpected data format for version {ver}")

        # Remove None values to keep the plot continuous
        values = [v for v in raw_values if v is not None]
        if not values:
            return types.TextContent(type="text", text=f"All values are None for version {ver}")

        series[ver] = values
        print(f"series: {series}")

    # Generate multi-line plot
    try:
        img_b64 = generate_multi_line_plot(series, metric, title_prefix=f"{config}: ")
    except ValueError as e:
        return types.TextContent(type="text", text=str(e))

    return types.ImageContent(type="image", data=img_b64.decode("utf-8"), mimeType="image/jpeg")


def _extract_regression_metrics(stdout: str) -> list[str]:
    """
    Parse Orion JSON output to identify metrics with significant changes at detected changepoints.
    
    Parameters:
    	stdout (str): JSON-formatted string output from Orion containing changepoint analysis.
    
    Returns:
    	list[str]: Descriptions of metrics that increased or decreased at changepoints, including the percentage change.
    """
    data = json.loads(stdout)
    metrics = []
    for dat in data:
        if not dat["is_changepoint"]:
            continue
        for metric in dat["metrics"]:
            percentage_change = dat["metrics"][metric]["percentage_change"]
            if percentage_change > 0:
                metrics.append(f"{metric} increased by {percentage_change:.2f}%")
            elif percentage_change < 0:
                metrics.append(f"{metric} decreased by {abs(percentage_change):.2f}%")

    return metrics


@mcp.tool()
async def has_openshift_regressed(
    version: Annotated[str, Field(description="Version of OpenShift to look into")] = "4.19",
    lookback: Annotated[str, Field(description="Number of days to lookback")] = "15",
) -> str:
    """
    Checks for performance regressions in the specified OpenShift version across all Orion configurations.
    
    Runs Orion analysis for each configuration and aggregates any detected changepoints indicating metric regressions. Returns a summary of affected configurations and metrics, or "No changepoints found" if no regressions are detected.
    
    Parameters:
        version (str): The OpenShift version to analyze.
        lookback (str): Number of days to look back for performance data.
    
    Returns:
        str: Summary of detected changepoints or a message indicating no regressions were found.
    """

    changepoints = []

    for config in ORION_CONFIGS:
        # Execute the command as a subprocess
        result = await run_orion(
            lookback=lookback,
            config=config,
            data_source=get_data_source(),
            version=version
        )

        if result.returncode not in (0, 3):
            metrics = _extract_regression_metrics(result.stdout)
            if metrics:
                changepoints.append(
                    f"⚠️ Change detected in configuration: '{config}'\n"
                    "Affected metrics:\n" +
                    "\n".join(f"  - {metric}" for metric in metrics)
                )

    if changepoints:
        return "\n\n".join(changepoints)
    return "No changepoints found"


# Correlation tool

@mcp.tool()
async def metrics_correlation(
    metric1: Annotated[str, Field(description="First metric to analyze")] = "podReadyLatency_P99",
    metric2: Annotated[str, Field(description="Second metric to analyze")] = "ovnCPU_avg",
    config: Annotated[str, Field(description="Config to analyze")] = "trt-external-payload-cluster-density.yaml",
    version: Annotated[str, Field(description="Version of OpenShift to look into")] = "4.19",
    lookback: Annotated[str, Field(description="Number of days to lookback")] = "15",
) -> types.ImageContent | types.TextContent:
    """
    Calculate and visualize the correlation between two specified metrics for a given Orion configuration and OpenShift version.
    
    Runs Orion analysis to extract values for both metrics, generates a scatter plot annotated with the Pearson correlation coefficient, and returns the plot as a base64-encoded JPEG image. If Orion execution fails or either metric is missing, returns a textual error message instead.
    
    Returns:
        ImageContent: Base64-encoded JPEG image of the correlation plot if successful.
        TextContent: Error message if Orion execution fails or metrics are missing.
    """

    # Run Orion to gather data
    result = await run_orion(
        lookback=lookback,
        config=ORION_CONFIGS_PATH + config,
        data_source=get_data_source(),
        version=version,
    )

    # Handle execution errors early
    if result.returncode not in (0, 3):
        summary_err = await summarize_result(result)
        return types.TextContent(type="text", text=f"Failed to execute Orion: {summary_err}")

    summary = await summarize_result(result)

    # Ensure we received a valid dict back
    if not isinstance(summary, dict):
        return types.TextContent(type="text", text=f"Error processing Orion output: {summary}")

    # Extract metric values
    try:
        values1 = summary[metric1]["value"]
        values2 = summary[metric2]["value"]
    except KeyError:
        return types.TextContent(
            type="text",
            text="Requested metrics not present in the Orion summary for the chosen configuration.",
        )

    # Compute correlation & generate plot
    corr_b64 = generate_correlation_plot(values1, values2, metric1, metric2, title_prefix=f"{config}: ")

    return types.ImageContent(type="image", data=corr_b64.decode("utf-8"), mimeType="image/jpeg")


def main():
    """Main function to run the MCP server."""
    # (No operation)


if __name__ == "__main__":
    if os.getenv("ES_SERVER") is None:
        print("ES_SERVER environment variable is not set")
        import sys
        sys.exit(1)
    TRANSPORT = "streamable-http"
    asyncio.run(mcp.run(transport=TRANSPORT))
    print("Running MCP server with transport:", TRANSPORT)

