"""
Functional APIs provided by the server
- /access/token
- /access/oneoff
- /access/revoke
- /record/{uuid}
- /graphql/query?mode=data/detail/facet/mri
- /graphql/pagination?search=<string>
- /filter?sidebar=<boolean>
- /collection
- /data/{action}/{filepath:path}?token=<token>
- /instance
- /dicom/export/{identifier}
"""
import copy
import io
import logging
import mimetypes
import re
import time

import psycopg2
import json

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi_utils.tasks import repeat_every
from pyorthanc import find

from app.config import Gen3Config, iRODSConfig
from app.data_schema import (
    ActionParam,
    CollectionItem,
    GraphQLPaginationItem,
    GraphQLQueryItem,
    IdentityItem,
    InstanceItem,
    ModeParam,
    access_revoke_responses,
    access_token_responses,
    collection_responses,
    filter_responses,
    instance_responses,
    one_off_access_responses,
    pagination_responses,
    query_responses,
    record_responses,
)
from app.function.filter.filter_editor import FilterEditor
from app.function.filter.filter_formatter import FilterFormatter
from app.function.filter.filter_generator import FilterGenerator
from app.function.filter.filter_logic import FilterLogic
from app.function.pagination.pagination_formatter import PaginationFormatter
from app.function.pagination.pagination_logic import PaginationLogic
from app.function.query.query_formatter import QueryFormatter
from app.function.query.query_logic import QueryLogic
from app.function.search.search_logic import SearchLogic
from middleware.auth import Authenticator
from services.external_service import ExternalService

logging.basicConfig()
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


app = FastAPI(
    title="12 Labours Portal",
    description="""
## Access

You will be able to:

* **Create Gen3 access based on authority**
* **Revoke Gen3 access**

## Gen3

You will be able to:

* **Get Gen3 program/project**
* **Get Gen3 node dictionary**
* **Get Gen3 record(s) metadata**
* **Use GraphQL query Gen3 metadata**
* **Download Gen3 metadata file**

## iRODS

You will be able to:

* **Get iRODS root/sub-folder(s)/sub-file(s)**
* **Download iRODS data file**

## Orthanc

You will be able to:

* **Get Orthanc dicom file instance ids**
* **Download Orthanc dicom file**
    """,
    contact={
        "name": "Auckland Bioengineering Institute",
        "url": "https://www.auckland.ac.nz/en/abi.html",
        # "email": "bioeng-enquiries@auckland.ac.nz",
    },
    openapi_tags=[
        {
            "name": "Gen3",
            "description": "Gen3 is a data platform for building data commons and data ecosystems",
            "externalDocs": {
                "description": "Gen3 official website",
                "url": "https://gen3.org/",
            },
        },
        {
            "name": "iRODS",
            "description": "iRODS is an open source data management software",
            "externalDocs": {
                "description": "iRODS official website",
                "url": "https://irods.org/",
            },
        },
        {
            "name": "Orthanc",
            "description": "Orthanc is a free and open-source, "
            + "lightweight DICOM server for medical imaging",
            "externalDocs": {
                "description": "Orthanc official website",
                "url": "https://www.orthanc-server.com/",
            },
        },
    ],
)

origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[
        "X-File-Name",
        "X-One-Off",
    ],
)

CONNECTION = None
FILTER_GENERATED = False
ES = ExternalService()
FE = FilterEditor()
FG = FilterGenerator(FE, ES)
FF = FilterFormatter(FE)
PF = PaginationFormatter(FE)
PL = PaginationLogic(FE, FilterLogic(), SearchLogic(ES), ES)
QF = QueryFormatter(FE)
QL = QueryLogic(ES)
A = Authenticator(ES)


@app.on_event("startup")
async def start_up():
    """
    Create service connection.
    """
    global CONNECTION
    CONNECTION = ES.check_service_status(True)
    logger.info(CONNECTION)


@app.on_event("startup")
@repeat_every(seconds=60 * 60 * 24)
def periodic_execution():
    """
    Update filter and cleanup users periodically.
    """
    global FILTER_GENERATED
    FILTER_GENERATED = False
    if CONNECTION["gen3"]:
        try:
            FILTER_GENERATED = FG.generate_public_filter()
        except Exception as error:
            logger.error("Invalid filter metadata %s has been used.", error)
        if FILTER_GENERATED:
            logger.info("Default filter has been updated.")
    else:
        logger.warning("Failed to update default filter.")

    if A.get_authorized_user_number() > 1:
        A.cleanup_authorized_user()


@app.get("/", tags=["Root"])
async def root():
    """
    Root
    """
    return "This is the 12 Labours Portal backend."


######################
### Access Control ###
######################


@app.post(
    "/access/token",
    tags=["Access"],
    summary="Create access token for authorized user",
    responses=access_token_responses,
)
async def create_access(
    item: IdentityItem,
    connection: dict = Depends(ES.check_service_status),
):
    """
    Return the access token.

    **Email**
    - email@gmail.com

    **Machine**
    - machine_id

    **Expiration**
    - expiration_time
    """
    if connection["gen3"] is None or connection["irods"] is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Please check the service (Gen3/iRODS) status",
        )
    if item.email is None or item.machine is None or item.expiration is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing one or more fields in the request body",
        )
    result = {
        "access_token": A.generate_access_token(item),
    }
    return result


@app.get(
    "/access/oneoff",
    tags=["Access"],
    summary="Create one off access token for authorized user",
    responses=one_off_access_responses,
)
async def create_one_off_access(
    authority: dict = Depends(A.handle_get_authority),
):
    """
    Return the one off access token.
    Used for preview and download endpoint.
    """
    result = {
        "one_off_token": authority["one_off_token"],
    }
    return result


@app.delete(
    "/access/revoke",
    tags=["Access"],
    summary="Revoke access for authorized user",
    responses=access_revoke_responses,
)
async def revoke_access(
    revoke: bool = Depends(A.handle_revoke_authority),
):
    """
    Return revoke message if success.
    """
    content = {
        "message": "Successfully revoke the access",
    }
    status_code = status.HTTP_200_OK
    if not revoke:
        content["message"] = "Unable to remove default access authority"
        # status_code = status.HTTP_401_UNAUTHORIZED
    return JSONResponse(status_code=status_code, content=content)


#########################
### Gen3 Data Commons ###
#########################


@app.get(
    "/record/{uuid}",
    tags=["Gen3"],
    summary="Get gen3 record information",
    responses=record_responses,
)
async def get_gen3_record(
    uuid: str,
    authority: dict = Depends(A.handle_get_authority),
    connection: dict = Depends(ES.check_service_status),
):
    """
    Return record information in the Gen3 Data Commons.

    - **uuid**: uuid of the record.
    """
    if connection["gen3"] is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Please check the service (Gen3) status",
        )

    def handle_access(access):
        access_list = access.split("-")
        return access_list[0], access_list[1]

    records = []
    # The uuid is unique, so there will only be zero or one record in all projects
    for access in authority["access_scope"]:
        program, project = handle_access(access)
        record = connection["gen3"].export_record(program, project, uuid, "json")
        if "message" not in record:
            records.append(record[0])
    if not records:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Data does not exist or unable to access the data",
        )

    return JSONResponse(
        content={"record": records[0]},
        headers={"X-One-Off": authority["one_off_token"]},
    )


def _handle_private_filter(access_scope):
    """
    Handler for generating private access and private filter
    """
    private_filter = {}
    if len(access_scope) > 1:
        private_access = copy.deepcopy(access_scope)
        private_access.remove(Gen3Config.GEN3_PUBLIC_ACCESS)
        private_filter = FG.generate_private_filter(private_access)
    return private_filter


@app.post(
    "/graphql/query",
    tags=["Gen3"],
    summary="GraphQL query gen3 metadata information",
    responses=query_responses,
)
async def get_gen3_graphql_query(
    item: GraphQLQueryItem,
    mode: ModeParam,
    authority: dict = Depends(A.handle_get_authority),
    connection: dict = Depends(ES.check_service_status),
):
    """
    Return queries metadata records. The API uses GraphQL query language.

    **node**
    - experiment_query
    - dataset_description_query
    - manifest_query
    - case_query

    **filter**
    - {"field_name": ["field_value", ...], ...}

    **search**
    - string content,
    - only available in dataset_description/manifest/case nodes
    """
    if connection["gen3"] is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Please check the service (Gen3) status",
        )
    if mode not in ["data", "detail", "facet", "mri"]:
        raise HTTPException(
            status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
            detail=f"The query mode ({mode}) is not provided in this API",
        )
    # Mode detail/facet/mri only be supported when query one dataset in experiment node
    # Use to pre-process the data
    if mode != "data" and (
        "submitter_id" not in item.filter or len(item.filter["submitter_id"]) > 1
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Mode {mode} only available when query one dataset in experiment node",
        )
    if item.node is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing field in the request body",
        )
    if item.node not in [
        "experiment_query",
        "dataset_description_query",
        "manifest_query",
        "case_query",
    ]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid query node is used",
        )
    if item.node == "experiment_query" and item.search != "":
        raise HTTPException(
            status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
            detail="Search does not provide in current node",
        )

    QF.set_query_mode(mode)
    QF.set_private_filter(_handle_private_filter(authority["access_scope"]))
    item.access = authority["access_scope"]
    query_result = QL.get_query_data(item)

    def handle_result():
        if len(query_result) == 1:
            return query_result[0]
        return query_result

    return JSONResponse(
        content=QF.process_data_output(handle_result()),
        headers={"X-One-Off": authority["one_off_token"]},
    )

@app.post(
    "/postgres/pagination",
    tags=["postgres"],
    summary="Display datasets",
    responses=pagination_responses,
)
# async def get_gen3_graphql_pagination(
async def get_postgres_pagination(
    numberPerPage: GraphQLPaginationItem,
    # search: str = "",
):
    conn, cur = get_postgres_connection()
    datasets = get_postgres_datasets(cur)

    print(datasets)

    items = []
    for dataset in datasets:
        dataset_id = dataset.get("dataset_id")
        dataset_uuid = dataset.get("dataset_uuid")
        dataset_name = dataset.get("dataset_name")

        preview_link = f"/data/preview/{dataset_name}/"
        # preview_link = f"/data/preview/{dataset_id}/"

        dataset_format = {
            "data_url_suffix": f"/data/browser/dataset/{dataset_id}?datasetTab=abstract",
            "source_url_middle": f"/data/download/{dataset_id}/",
            "contributors": PF._handle_contributor(
                dataset.get("contributor_name")
            ),
            "keywords": dataset.get("keywords"),
            "numberSamples": dataset.get("number_of_samples"),
            "numberSubjects": dataset.get("number_of_subjects"),
            "name": dataset.get("title"),
            "datasetId": dataset_id,
            "organs": dataset.get("study_organ_system"),
            # "species": PF._handle_species(_["cases"]),
            # "plots": PF._handle_manifest(dataset_uuid, preview_link, _["plots"]),
            # "scaffoldViews": PF._handle_manifest(
            #     dataset_uuid, preview_link, _["scaffoldViews"], True
            # ),
            # "scaffolds": PF._handle_manifest(dataset_uuid, preview_link, _["scaffolds"]),
            "thumbnails": get_thumbnails(preview_link),
            # "thumbnails": PF._handle_manifest(
            #     dataset_name,
            #     preview_link,
            #     PF._handle_thumbnail(dataset["thumbnails"]),
            #     True,
            # ),
            # "mris": PF._handle_manifest(dataset_uuid, preview_link, _["mris"]),
            # "dicomImages": PF._handle_manifest(
            #     dataset_uuid, preview_link, _["dicomImages"]
            # ),
            "category": dataset.get("category"),
            "detailsReady": True
        }
        items.append(dataset_format)


    data_count = len(items)  # number of datasets
    return JSONResponse(
        content={
            "items": items,
            "numberPerPage": 1000,
            "total": data_count,
        },
    )

def get_thumbnails(preview_link):
    thumbnails_url = preview_link + "primary/thumbnail_0.jpg"
    thumbnails = [
        {"image_url": thumbnails_url}
    ]
    return thumbnails

def get_postgres_connection():
    conn = psycopg2.connect(
        host="localhost",
        port="5432",
        database="digitaltwins",
        user="postgres",
        password="postgres")
    cur = conn.cursor()

    return conn, cur
def get_postgres_datasets(cur):
    # sql = "SELECT * FROM dataset"
    # sql = ("SELECT * FROM dataset "
    #        "LEFT JOIN dataset_description ON dataset.dataset_uuid = dataset_description.dataset_uuid")
    # where project = XXX
    # RIGHT JOIN project ON dataset.project_uuid = project.project_uuid;
    # a611bd1a-5f4d-11ef-917d-484d7e9beb16
    # 9fce33d4-5f4d-11ef-917d-484d7e9beb16
    sql = ("SELECT * FROM dataset "
           "LEFT JOIN dataset_description ON dataset.dataset_uuid = dataset_description.dataset_uuid "
           "RIGHT JOIN project ON dataset.project_uuid = project.project_uuid "
           "WHERE project.project_uuid IN ('a611bd1a-5f4d-11ef-917d-484d7e9beb16', '9fce33d4-5f4d-11ef-917d-484d7e9beb16')")


    cur.execute(sql)
    resp = cur.fetchall()
    datasets = resp

    column_names = []

    # Iterate over the cursor description to extract column names
    for desc in cur.description:
        column_name = desc[0]  # Get the column name from the description tuple
        column_names.append(column_name)  # Add the column name to the list

    datasets_formated = []
    for dataset in datasets:
        row_dict = {}
        for i in range(len(dataset)):
            column_name = column_names[i]
            row_value = dataset[i]
            # Add the column name and value to the dictionary
            row_dict[column_name] = row_value
        # Convert the dictionary to a JSON object and add it to the list
        # datasets_formated.append(json.dumps(row_dict))
        datasets_formated.append(row_dict)

    return datasets_formated


@app.post(
    "/graphql/pagination",
    tags=["Gen3"],
    summary="Display datasets",
    responses=pagination_responses,
)
async def get_gen3_graphql_pagination(
    item: GraphQLPaginationItem,
    search: str = "",
    authority: dict = Depends(A.handle_get_authority),
    connection: dict = Depends(ES.check_service_status),
):
    """
    /graphql/pagination/?search=<string>

    Return filtered/searched metadata records. The API uses GraphQL query language.

    - Default page = 1
    - Default limit = 50
    - Default filter = {}
    - Default search = ""
    - Default relation = "and"
    - Default access = gen3 public access repository
    - Default order = "published(asc)"

    **node**
    - experiment_pagination

    **filter(zero or more)**
    - {"gen3_node>gen3_field": [filter_name,...], ...}

    **search(parameter)**:
    - string content
    """
    if connection["gen3"] is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Please check the service (Gen3) status",
        )
    if search and connection["irods"] is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Please check the service (iRODS) status",
        )

    PL.set_private_filter(_handle_private_filter(authority["access_scope"]))
    item.access = authority["access_scope"]
    is_public_access_filtered = PL.process_pagination_item(item, search)
    data_count, match_pair = PL.get_pagination_count(item)
    query_result = PL.get_pagination_data(item, match_pair, is_public_access_filtered)
    # If both asc and desc are None, datasets ordered by self-written order function
    if item.asc is None and item.desc is None:
        query_result = sorted(
            query_result,
            key=lambda dict: item.filter["submitter_id"].index(dict["submitter_id"]),
        )
    return JSONResponse(
        content={
            "items": PF.reconstruct_data_structure(query_result),
            "numberPerPage": item.limit,
            "total": data_count,
        },
        headers={"X-One-Off": authority["one_off_token"]},
    )


@app.get(
    "/filter",
    tags=["Gen3"],
    summary="Get filter information",
    responses=filter_responses,
)
async def get_gen3_filter(
    sidebar: bool = False,
    authority: dict = Depends(A.handle_get_authority),
    connection: dict = Depends(ES.check_service_status),
):
    """
    /filter/?sidebar=<boolean>

    Return the support data for portal filters component.

    - **sidebar**: boolean content.
    """
    if connection["gen3"] is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Please check the service (Gen3) status",
        )

    retry = 0
    while retry < 12 and not FILTER_GENERATED:
        retry += 1
        time.sleep(retry)
    FF.set_private_filter(_handle_private_filter(authority["access_scope"]))
    if sidebar:
        return FF.generate_sidebar_filter_format()
    return FF.generate_filter_format()


############################################
### Integrated Rule-Oriented Data System ###
############################################


IRODS_REQUEST = {
    "endpoint": None,
    "additional": None,
}


def _handle_irods_access(endpoint, path, access_scope):
    global PREVIOUS_REQUEST
    dataset = list(filter(None, path.split("/")))
    filter_ = {}
    # Query specific dataset if submitter id exist
    if dataset:
        if (
            IRODS_REQUEST["endpoint"] == endpoint
            and IRODS_REQUEST["additional"] == dataset
        ):
            return
        filter_["submitter_id"] = dataset
    query_item = GraphQLQueryItem(
        node="experiment_filter",
        filter=filter_,
        access=access_scope,
    )
    query_result = ES.get("gen3").process_graphql_query(query_item)
    accessible = list(map(lambda d: d["submitter_id"], query_result))
    if not accessible:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unable to access the data",
        )

    IRODS_REQUEST["endpoint"] = endpoint
    IRODS_REQUEST["additional"] = dataset
    return accessible


@app.post(
    "/collection",
    tags=["iRODS"],
    summary="Get folder information",
    responses=collection_responses,
)
async def get_irods_collection(
    item: CollectionItem,
    authority: dict = Depends(A.handle_get_authority),
    connection: dict = Depends(ES.check_service_status),
):
    """
    Return all collections from the required folder.

    Root folder will be returned if no item or "/" is passed.
    """
    if connection["gen3"] is None or connection["irods"] is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Please check the service (Gen3/iRODS) status",
        )
    if not re.match("(/(.)*)+", item.path):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid path format is used",
        )

    accessible = _handle_irods_access(
        "/collection", item.path, authority["access_scope"]
    )

    def handle_collection(data):
        collection = []
        for ele in data:
            if item.path == "/" and ele.name not in accessible:
                continue
            collection.append(
                {
                    "name": ele.name,
                    "path": re.sub(iRODSConfig.IRODS_ROOT_PATH, "", ele.path),
                }
            )
        return collection

    try:
        coll = connection["irods"].collections.get(
            f"{iRODSConfig.IRODS_ROOT_PATH}{item.path}"
        )
        result = {
            "folders": handle_collection(coll.subcollections),
            "files": handle_collection(coll.data_objects),
        }
        return result
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Data not found in the provided path",
        ) from error


@app.get(
    "/data/{action}/{filepath:path}",
    tags=["iRODS"],
    summary="Download irods file",
    response_description="Successfully return a file with data",
)
async def get_irods_data_file(
    action: ActionParam,
    filepath: str,
    token: str = None,
    connection: dict = Depends(ES.check_service_status),
):
    """
    Used to preview most types of data files in iRODS (.xlsx and .csv not supported yet).
    OR
    Return a specific download file from iRODS or a preview of most types data.

    - **action**: Action should be either preview or download.
    - **filepath**: Required iRODS file path.
    """
    chunk_size = 1024 * 1024 * 1024

    # if connection["gen3"] is None or connection["irods"] is None:
    #     raise HTTPException(
    #         status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    #         detail="Please check the service (Gen3/iRODS) status",
    #     )
    # if action not in ["preview", "download"]:
    #     raise HTTPException(
    #         status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
    #         detail=f"The action ({action}) is not provided in this API",
    #     )
    #
    # access_scope = A.handle_get_one_off_authority(token)
    # _handle_irods_access(f"/data/{action}", filepath, access_scope)

    try:
        file = connection["irods"].data_objects.get(
            f"{iRODSConfig.IRODS_ROOT_PATH}/{filepath}"
        )
        filename = file.name
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Data not found in the provided path",
        ) from error

    def handle_header():
        header = None
        if action == "download":
            header = {
                "X-File-Name": filename,
                "Content-Disposition": f"attachment;filename={filename}",
            }
        return header

    def handle_mimetype():
        return mimetypes.guess_type(filename)[0]

    def iterate_file():
        with file.open("r") as file_like:
            chunk = file_like.read(chunk_size)
            while chunk:
                yield chunk
                chunk = file_like.read(chunk_size)

    return StreamingResponse(
        iterate_file(), media_type=handle_mimetype(), headers=handle_header()
    )


##############################
### Orthanc - DICOM server ###
##############################


@app.post(
    "/instance",
    tags=["Orthanc"],
    summary="Get instance ids",
    responses=instance_responses,
)
async def get_orthanc_instance(
    item: InstanceItem,
    connection: dict = Depends(ES.check_service_status),
):
    """
    Return a list of dicom instance uuids
    """
    if connection["orthanc"] is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Please check the service (Orthanc) status",
        )
    if item.study is None or item.series is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing one or more fields in the request body",
        )

    patients = find(
        orthanc=connection["orthanc"],
        study_filter=lambda s: s.uid == item.study,
        series_filter=lambda s: s.uid == item.series,
    )
    if not patients:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resource is not found in the orthanc server",
        )

    result = []
    for patient in patients:
        for study in patient.studies:
            for series in study.series:
                for instance in series.instances:
                    result.append(instance.id_)
    return result


@app.get(
    "/dicom/export/{identifier}",
    tags=["Orthanc"],
    summary="Export dicom file",
    response_description="Successfully return a file with data",
)
async def get_orthanc_dicom_file(
    identifier: str,
    connection: dict = Depends(ES.check_service_status),
):
    """
    Export a specific dicom file from Orthanc server

    - **identifier**: dicom instance uuid.
    """
    if connection["orthanc"] is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Please check the service (Orthanc) status",
        )

    try:
        instance_file = connection["orthanc"].get_instances_id_file(identifier)
        bytes_file = io.BytesIO(instance_file)
        return Response(bytes_file.getvalue(), media_type="application/dicom")
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resource is not found in the orthanc server",
        ) from error
