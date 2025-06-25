# Orion MCP

Orion MCP is a Model Context Protocol (MCP) server for performance regression analysis using the [cloud-bulldozer/orion](https://github.com/cloud-bulldozer/orion) library.

## Table of Contents
- [Features](#features)
- [Requirements](#requirements)
- [Build](#build)
- [Run](#run)
- [Deploying on OpenShift](#deploying-on-openshift)
- [Usage](#usage)
- [Contributing](#contributing)
- [License](#license)

## Features
- Run performance regression analysis on OpenShift and Kubernetes clusters
- Generates visualization(s) based on input from the user
- Expose MCP tools and resources for automation and integration

## Requirements
- Python 3.11+
- [orion](https://github.com/cloud-bulldozer/orion) (installed or available via Podman)
- Podman (if running Orion in a container)
- OpenSearch or Elasticsearch instance for data source
- (Optional) Docker/Podman for containerized builds

## Build

1. **Clone the repository:**
   ```bash
   git clone https://github.com/YOUR_USERNAME/orion-mcp.git
   cd orion-mcp
   ```

2. **Install dependencies:**
   ```bash
   python3.11 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

3. **(Optional) Build container image:**
   ```bash
   podman build -t orion-mcp .
   # or
   docker build -t orion-mcp .
   ```

## Run

1. **Set the required environment variable:**
   ```bash
   export ES_SERVER="http://your-opensearch-url:9200"
   ```

2. **Run the MCP server:**
   ```bash
   python orion_mcp.py
   ```
   Or with the container:
   ```bash
   podman run --env ES_SERVER=$ES_SERVER -p 3030:3030 orion-mcp
   # or
   docker run --env ES_SERVER=$ES_SERVER -p 3030:3030 orion-mcp
   ```
   
## Deploying on OpenShift

You can deploy `orion-mcp` on OpenShift using the provided `openshift-deployment.yml` manifest. Make sure to update the `ES_SERVER` environment variable to point to your OpenSearch or Elasticsearch instance.

### Steps

1. **Edit the deployment manifest:**
   
   Open `openshift-deployment.yml` and update the following section with your OpenSearch/Elasticsearch URL:
   
   ```yaml
   env:
     - name: ES_SERVER
       value: "http://your-opensearch-url:9200"
   ```

2. **Apply the deployment:**
   
   ```bash
   oc apply -f openshift-deployment.yml
   # or
   kubectl apply -f openshift-deployment.yml
   ```

3. **Verify the deployment:**
   
   ```bash
   oc get pods -l app.kubernetes.io/name=orion-mcp
   ```

4. **Access the MCP server:**
   
   Expose the service as needed (e.g., via `oc expose` or an OpenShift Route) to access the MCP server on port 3030.


## Usage
- The MCP server exposes tools and resources for performance analysis.
- You can interact with the server via HTTP or integrate it into your automation workflows.
- Example config files for Orion can be found in the [orion examples directory](https://github.com/cloud-bulldozer/orion/blob/main/examples/).

## Contributing

Contributions are welcome! To contribute:

1. Fork the repository
2. Create a new branch for your feature or bugfix
3. Make your changes and add tests if applicable
4. Run linting and tests:
   ```bash
   flake8
   pytest
   ```
5. Submit a pull request with a clear description of your changes

Please follow the [Contributor Covenant Code of Conduct](https://www.contributor-covenant.org/version/2/1/code_of_conduct/) in all interactions.

## License

This project is licensed under the Apache License. See [LICENSE](LICENSE) for details.

