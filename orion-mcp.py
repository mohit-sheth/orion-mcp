# orion-mcp.py
# A Model Context Protocol (MCP) server that provides a tool for running
# performance regression analysis using the cloud-bulldozer/orion library.

import asyncio
from typing import Annotated, AsyncGenerator
from pydantic import Field

import mcp.types as types
from typing import Literal, Optional
from mcp.server.fastmcp import FastMCP, Context

# Import utility functions from utils module
from utils.utils import (
    run_orion,
    convert_results_to_csv,
    csv_to_graph,
    summarize_result,
    get_data_source
)

mcp = FastMCP(name="orion-mcp",
              host="0.0.0.0",
              port=3030,
              log_level='INFO')

@mcp.resource("orion-mcp://get_data_source")
def get_data_source_resource() -> str:
    """
    provides the data source url for orion analysis. user must launch mcp 
    server with the environment variable es_server set to the opensearch url.

    args:
        data_source: The OpenSearch URL where performance data is stored.

    Returns:
        The OpenSearch URL as a string.
    """
    return get_data_source()

@mcp.tool()
async def openshift_detailed_performance(
    version: Annotated[str, Field(description="Version of OpenShift to look into")] = "4.19",
    lookback: Annotated[str, Field(description="Number of days to lookback")] = "15",
) -> types.ImageContent | types.TextContent : 
    """
    Captures a performance analysis against the specified OpenShift version using Orion 
    and provides visual report.

    Orion uses an EDivisive algorithm to analyze performance data from a specified
    configuration file to detect any performance regressions.

    Args:
        version: Openshift version to look into.
        lookback: The number of days to look back for performance data. Defaults to 15 days.

    Returns:
        Returns a list of images showing the performance overtime.
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
        result = await run_orion(
            lookback=lookback,
            config=config,
            data_source=get_data_source(),
            version=version
        )
        if result.returncode != 0:
            # If there's an error, return error images
            error_results = [{"error": summarize_result(result)}]
            b64_imgs = await csv_to_graph(convert_results_to_csv(error_results))
            imgs = []
            for img in b64_imgs:
                if img is None:
                    continue
                b64_img = img.decode('utf-8')
                imgs.append(types.ImageContent(type="image", data=b64_img, mimeType="image/jpeg"))
            return imgs[0]

        data[config] = {}
        data[config] = await summarize_result(result)
        results.append(data)
    
    # Process results and return all images
    b64_imgs = await csv_to_graph(convert_results_to_csv(results))
    imgs = []
    for img in b64_imgs:
        if img is None:
            continue
        b64_img = img.decode('utf-8')
        imgs.append(types.ImageContent(type="image", data=b64_img, mimeType="image/jpeg"))
    
    return imgs[0]

@mcp.tool()
async def has_openshift_regressed(
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

    for i, config in enumerate(orion_configs):
        # Execute the command as a subprocess
        result = await run_orion(
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
    asyncio.run(mcp.run(transport=transport))
    print("Running MCP server with transport:", transport)