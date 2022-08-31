import json
import requests
import mimetypes

from app.config import Config, Gen3Config, iRODSConfig
from app.dbtable import StateTable

from typing import Union

from fastapi import FastAPI, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, HTMLResponse, FileResponse, StreamingResponse
from pydantic import BaseModel

from irods.session import iRODSSession

app = FastAPI()

# Cross orgins, allow any for now
origins = [
    '*',
]

# Add CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

statetable = None

# CORS(app)

BAD_REQUEST = 400
NOT_FOUND = 404

GEN3_CREDENTIALS = {
    "api_key": Gen3Config.GEN3_API_KEY,
    "key_id": Gen3Config.GEN3_KEY_ID
}

HEADER = None
SESSION = None


class S3Item(BaseModel):
    suffix: Union[str, None] = None


class RecordItem(BaseModel):
    program: Union[str, None] = None
    project: Union[str, None] = None
    format: Union[str, None] = None


class GraphQLItem(BaseModel):
    node_type: Union[str, None] = None
    filter: Union[str, None] = None
    search: Union[str, None] = None
    field: Union[str, None] = None


class CollectionItem(BaseModel):
    path: Union[str, None] = None


@app.on_event("startup")
async def start_up():
    try:
        global statetable
        statetable = StateTable(Config.DATABASE_URL)
    except AttributeError:
        print("Encounter an error setting up the database")
        statetable = None

    try:
        global HEADER
        TOKEN = requests.post(
            f"{Gen3Config.GEN3_ENDPOINT_URL}/user/credentials/cdis/access_token", json=GEN3_CREDENTIALS).json()
        HEADER = {"Authorization": "bearer " + TOKEN["access_token"]}
    except Exception:
        print("Encounter an error while generating a token from GEN3")

    try:
        # This function is used to connect to the iRODS server, it requires "host", "port", "user", "password" and "zone" environment variables.
        global SESSION
        SESSION = iRODSSession(host=iRODSConfig.IRODS_HOST,
                               port=iRODSConfig.IRODS_PORT,
                               user=iRODSConfig.IRODS_USER,
                               password=iRODSConfig.IRODS_PASSWORD,
                               zone=iRODSConfig.IRODS_ZONE)
        # SESSION.connection_timeout =
    except Exception:
        print("Encounter an error while creating the iRODS session")


@app.get("/", response_class=PlainTextResponse)
async def root():
    return "This is the fastapi backend."


@app.get("/health", response_class=PlainTextResponse)
async def health():
    return json.dumps({"status": "healthy"})


def get_share_link(table):
    # Do not commit to database when testing
    commit = True
    if app.config["TESTING"]:
        commit = False
    if table:
        json_data = request.get_json()
        if json_data and "state" in json_data:
            state = json_data["state"]
            uuid = table.pushState(state, commit)
            return {"uuid": uuid}
        abort(400, description="State not specified")
    else:
        abort(404, description="Database not available")


def get_saved_state(table):
    if table:
        json_data = request.get_json()
        if json_data and "uuid" in json_data:
            uuid = json_data["uuid"]
            state = table.pullState(uuid)
            if state:
                return {"state": table.pullState(uuid)}
        abort(400, description="Key missing or did not find a match")
    else:
        abort(404, description="Database not available")


# An example
@app.put("/state/getshareid")
async def get_share_link():
    return get_share_link(statetable)


# Get the map state using the share link id.
@app.get("/state/getstate")
async def get_state():
    return get_saved_state(statetable)


#
# Gen3 Data Commons
#


@app.get("/program")
# Get the program information from Gen3 Data Commons
async def get_gen3_program():
    """
    Return the program information from Gen3 Data Commons
    """
    res = requests.get(
        f"{Gen3Config.GEN3_ENDPOINT_URL}/api/v0/submission/", headers=HEADER)
    try:
        res.raise_for_status()
        json_data = json.loads(res.content)
        program_list = []
        for ele in json_data["links"]:
            program_list.append(ele.replace(
                "/v0/submission/", ""))
        new_json_data = {"program": program_list}
        return new_json_data
    except Exception as e:
        raise HTTPException(status_code=NOT_FOUND, detail=str(e))


@app.get("/project/{program}")
# Get all projects information from Gen3 Data Commons
async def get_gen3_project(program: str):
    """
    Return project information.

    :param program: Gen3 program name
    """
    res = requests.get(
        f"{Gen3Config.GEN3_ENDPOINT_URL}/api/v0/submission/{program}", headers=HEADER)
    try:
        res.raise_for_status()
        json_data = json.loads(res.content)
        project_list = []
        for ele in json_data["links"]:
            project_list.append(ele.replace(
                f"/v0/submission/{program}/", ""))
        new_json_data = {"project": project_list}
        return new_json_data
    except Exception as e:
        raise HTTPException(status_code=res.status_code, detail=str(e))


@app.get("/dictionary")
# Get all dictionary node from Gen3 Data Commons
async def get_gen3_dictionary():
    """
    Return all dictionary node from Gen3 Data Commons
    """
    res = requests.get(
        f"{Gen3Config.GEN3_ENDPOINT_URL}/api/v0/submission/_dictionary", headers=HEADER)
    try:
        res.raise_for_status()
        json_data = json.loads(res.content)
        dictionary_list = []
        for ele in json_data["links"]:
            dictionary_list.append(ele.replace(
                "/v0/submission/_dictionary/", ""))
        new_json_data = {"dictionary": dictionary_list}
        return new_json_data
    except Exception as e:
        raise HTTPException(status_code=res.status_code, detail=str(e))


def is_json(json_data):
    """
    Returns true if the given string is a valid json.

    :param json_data: The input data need to be checked.
    :return: True if the string can be parsed as valid json.
    """
    try:
        json.loads(json_data)
    except ValueError:
        return False
    return True


@app.post("/records/{node_type}")
# Exports all records in a dictionary node
async def get_gen3_node_records(node_type: str, item: RecordItem):
    """
    Return all records in a dictionary node.

    :param node_type: The dictionary node to export.
    :return: A list of json object containing all records in the dictionary node.
    """
    if item.program == None or item.project == None or item.format == None:
        raise HTTPException(status_code=BAD_REQUEST,
                            detail="Missing one ore more fields in request body")

    res = requests.get(
        f"{Gen3Config.GEN3_ENDPOINT_URL}/api/v0/submission/{item.program}/{item.project}/export/?node_label={node_type}&format={item.format}", headers=HEADER)
    try:
        res.raise_for_status()
        json_data = json.loads(res.content)
        if is_json(res.content) and "data" in json_data and json_data["data"] != []:
            return json_data
        else:
            raise HTTPException(status_code=NOT_FOUND,
                                detail="Records cannot be found")
    except Exception as e:
        raise HTTPException(status_code=res.status_code, detail=str(e))


@app.post("/record/{uuids}")
# Exports one or more records(records must in one node), use comma to separate the uuids
# e.g. uuid1,uuid2,uuid3
async def get_gen3_record(uuids: str, item: RecordItem):
    """
    Return the fields of one or more records in a dictionary node.

    :param uuids: uuids of the records (use comma to separate the uuids e.g. uuid1,uuid2,uuid3).
    :return: A list of json object
    """
    if item.program == None or item.project == None or item.format == None:
        raise HTTPException(status_code=BAD_REQUEST,
                            detail="Missing one ore more fields in request body")

    res = requests.get(
        f"{Gen3Config.GEN3_ENDPOINT_URL}/api/v0/submission/{item.program}/{item.project}/export/?ids={uuids}&format={item.format}", headers=HEADER)
    try:
        res.raise_for_status()
        json_data = json.loads(res.content)
        if b"id" in res.content:
            return json_data
        else:
            raise HTTPException(status_code=NOT_FOUND,
                                detail="Record can not be found, please check the uuid of the record")
    except Exception as e:
        raise HTTPException(status_code=res.status_code, detail=str(e))


@app.post("/graphql")
# Only used for filtering the files in a specific node for now
async def graphql_query(item: GraphQLItem):
    """
    Return filtered metadata records. The query uses GraphQL query.

    filter post format should looks like:
    '<field_name>: ["<attribute_name>"], ...], ...'

    search post format should looks like:
    '"<keyword>"'

    Field post format should looks like:
    "<field_name> <field_name> <field_name> ..."
    """
    if item.node_type == None or item.search == None or item.field == None:
        raise HTTPException(status_code=BAD_REQUEST,
                            detail="Missing one ore more fields in request body")

    if item.filter == None:
        filter = ""
    else:
        filter = item.filter
    query = {
        "query":
        """{""" +
        f"""{item.node_type}({filter}, quick_search: {item.search})""" +
        """{""" +
        f"""{item.field}""" +
        """}""" +
        """}"""
    }

    res = requests.post(
        f"{Gen3Config.GEN3_ENDPOINT_URL}/api/v0/submission/graphql/", json=query, headers=HEADER)
    try:
        res.raise_for_status()
        json_data = json.loads(res.content)
        if json_data["data"][f"{item.node_type}"] != []:
            return json_data
        else:
            raise HTTPException(status_code=NOT_FOUND,
                                detail="Data cannot be found in current node")
    except Exception as e:
        raise HTTPException(status_code=res.status_code, detail=str(e))


@app.get("/download/metadata/{program}/{project}/{uuid}/{format}/{filename}")
async def download_gen3_metadata_file(program: str, project: str, uuid: str, format: str, filename: str):
    """
    Return a single file for a given uuid.

    :param program: program name.
    :param project: project name.
    :param uuid: uuid of the file.
    :param format: format of the file (must be one of the following: json, tsv).
    :param filename: name of the file.
    :return: A JSON or CSV file containing the metadata.
    """
    res = requests.get(
        f"{Gen3Config.GEN3_ENDPOINT_URL}/api/v0/submission/{program}/{project}/export/?ids={uuid}&format={format}", headers=HEADER)
    try:
        res.raise_for_status()
        if format == "json":
            return Response(content=res.content,
                            media_type="application/json",
                            headers={"Content-Disposition":
                                     f"attachment;filename={filename}.json"})
        else:
            return Response(content=res.content,
                            media_type="text/csv",
                            headers={"Content-Disposition":
                                     f"attachment;filename={filename}.csv"})
    except Exception as e:
        raise HTTPException(status_code=NOT_FOUND, detail=str(e))


#
# iRODS
#


def get_data_list(collect):
    collect_list = []
    for ele in collect:
        collect_list.append({
            "id": ele.id,
            "name": ele.name,
            "path": ele.path
        })
    return collect_list


@app.get("/collection")
async def get_irods_root_collections():
    """
    Return all collections from the root folder.
    """
    try:
        collect = SESSION.collections.get(
            f"{iRODSConfig.IRODS_ENDPOINT_URL}")
    except Exception as e:
        raise HTTPException(status_code=NOT_FOUND, detail=str(e))
    folders = get_data_list(collect.subcollections)
    files = get_data_list(collect.data_objects)
    return {"folders": folders, "files": files}


@app.post("/collection")
async def get_irods_collections(item: CollectionItem):
    """
    Return all collections from the required folder.
    """
    if item.path == None:
        raise HTTPException(status_code=BAD_REQUEST,
                            detail="Missing field in request body")

    try:
        collect = SESSION.collections.get(item.path)
        folders = get_data_list(collect.subcollections)
        files = get_data_list(collect.data_objects)
        return {"folders": folders, "files": files}
    except Exception as e:
        raise HTTPException(status_code=NOT_FOUND, detail=str(e))


@app.get("/preview/data/{suffix}")
async def preview_irods_data_file(suffix: str):
    """
    Used to preview most types of the data file.

    :param suffix: Required iRODS file path.
    """
    url_suffix = suffix.replace("&", "/")
    try:
        file = SESSION.data_objects.get(
            f"{iRODSConfig.IRODS_ENDPOINT_URL}/{url_suffix}")

        def iterfile():
            with file.open("r") as file_like:
                yield from file_like
        return StreamingResponse(iterfile(), media_type=mimetypes.guess_type(file.name)[0])
    except Exception as e:
        raise HTTPException(status_code=NOT_FOUND, detail=str(e))


@app.get("/download/data/{suffix}")
async def download_irods_data_file(suffix: str):
    """
    Return a specific download file from iRODS.

    :param suffix: Required iRODS file path.
    :return: A file with data.
    """
    url_suffix = suffix.replace("&", "/")
    try:
        file = SESSION.data_objects.get(
            f"{iRODSConfig.IRODS_ENDPOINT_URL}/{url_suffix}")
        with file.open("r") as f:
            content = f.read()
        return Response(content=content,
                        media_type=mimetypes.guess_type(file.name)[0],
                        headers={"Content-Disposition":
                                 f"attachment;filename={file.name}"})
    except Exception as e:
        raise HTTPException(status_code=NOT_FOUND, detail=str(e))