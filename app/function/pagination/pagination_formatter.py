"""
Functionality for reconstructing data structure
- reconstruct_data_structure
"""
import json
import re


class PaginationFormatter:
    """
    fe -> filter editor object is required
    """

    def __init__(self, fe):
        self.__filter_cache = fe.cache_loader()

    def _handle_thumbnail(self, data):
        """
        Handler for updating thumbnail
        """
        result = []
        if data == []:
            return result
        for _ in data:
            if _["additional_types"] is None:
                result.append(_)
        return result

    def _handle_multiple_cite_path(self, filename, cite):
        """
        Handler for updating multiple cite path
        """
        full_path = ""
        result = {"path": [], "relative": {"path": []}}
        data = json.loads(re.sub("'", '"', cite))
        for _ in data:
            full_path_list = filename.split("/")
            full_path_list[-1] = _.split("/")[-1]
            full_path = "/".join(full_path_list)
            result["path"].append(full_path)
            result["relative"]["path"].append(_.split("/")[-1])
        return result

    # filename: (contains full file path), cite: isDerivedFrom/isDescribedBy/isSourceOf
    def _handle_cite_path(self, filename, cite):
        """
        Handler for updating cite path
        """
        cite_path = self._handle_empty(cite)
        full_path = ""
        result = {"path": [], "relative": {"path": []}}
        if cite_path != "":
            if len(cite_path.split(",")) > 1:
                result = self._handle_multiple_cite_path(filename, cite_path)
                return result
            full_path_list = filename.split("/")
            full_path_list[-1] = cite_path.split("/")[-1]
            full_path = "/".join(full_path_list)
        result["path"].append(full_path)
        result["relative"]["path"].append(cite_path.split("/")[-1])
        return result

    def _handle_empty(self, data):
        """
        Handler for updating empty value
        """
        if data is None or data == "NA":
            return ""
        return data

    def _handle_image_link(self, preview_link, filename, is_source_of, has_image):
        """
        Handler for updating the image url
        """
        result = preview_link
        if has_image:
            if is_source_of != "":
                path_list = filename.split("/")
                path_list[-1] = is_source_of.split("/")[-1]
                filepath = "/".join(path_list)
                result += filepath
            else:
                result += filename
        else:
            return ""
        return result

    def _handle_manifest(self, uuid, preview_link, data, has_image=False):
        """
        Handler for updating the data format which be queried based on manifest structure
        """
        result = []
        for _ in data:
            filename = _["filename"]
            item = {
                "image_url": self._handle_image_link(
                    preview_link,
                    filename,
                    self._handle_empty(_["is_source_of"]),
                    has_image,
                ),
                "additional_mimetype": {
                    "name": self._handle_empty(_["additional_types"])
                },
                "datacite": {
                    "isDerivedFrom": self._handle_cite_path(
                        filename, _["is_derived_from"]
                    ),
                    "isDescribedBy": self._handle_cite_path(
                        filename, _["is_described_by"]
                    ),
                    "isSourceOf": self._handle_cite_path(filename, _["is_source_of"]),
                    "supplemental_json_metadata": {
                        "description": self._handle_empty(
                            _["supplemental_json_metadata"]
                        )
                    },
                },
                "dataset": {
                    "identifier": uuid,
                    "path": filename,
                },
                "file_type": {
                    "name": self._handle_empty(_["file_type"]),
                },
                "identifier": _["id"],
                "name": filename.split("/")[-1],
            }
            result.append(item)
        return result

    def _handle_species(self, data):
        """
        Handler for updating the species format
        """
        result = []
        if data == []:
            return result
        for _ in data:
            if _["species"] != "NA":
                species_filter = self.__filter_cache["MAPPED_SPECIES"]["facets"]
                facets = species_filter.values()
                if _["species"] in facets:
                    index = list(facets).index(_["species"])
                    species = list(species_filter.keys())[index]
                else:
                    species = _["species"]
                if species not in result:
                    result.append(species)
        return result

    def _handle_contributor(self, data):
        """
        Handler for updating the contributor format
        """
        result = []
        if data == [] or data is None:
            return result
        for _ in data:
            contributor = {"name": _}
            result.append(contributor)
        return result

    def reconstruct_data_structure(self, data):
        """
        Reconstructing the structure to support portal services
        """
        result = []
        for _ in data:
            dataset_description = _["dataset_descriptions"][0]
            submitter_id = _["submitter_id"]
            uuid = _["id"]
            preview_link = f"/data/preview/{submitter_id}/"
            dataset_format = {
                "data_url_suffix": f"/data/browser/dataset/{submitter_id}?datasetTab=abstract",
                "source_url_middle": f"/data/download/{submitter_id}/",
                "contributors": self._handle_contributor(
                    dataset_description["contributor_name"]
                ),
                "keywords": dataset_description["keywords"],
                "numberSamples": int(dataset_description["number_of_samples"][0]),
                "numberSubjects": int(dataset_description["number_of_subjects"][0]),
                "name": dataset_description["title"][0],
                "datasetId": submitter_id,
                "organs": dataset_description["study_organ_system"],
                "species": self._handle_species(_["cases"]),
                "plots": self._handle_manifest(uuid, preview_link, _["plots"]),
                "scaffoldViews": self._handle_manifest(
                    uuid, preview_link, _["scaffoldViews"], True
                ),
                "scaffolds": self._handle_manifest(uuid, preview_link, _["scaffolds"]),
                "thumbnails": self._handle_manifest(
                    uuid,
                    preview_link,
                    self._handle_thumbnail(_["thumbnails"]),
                    True,
                ),
                "mris": self._handle_manifest(uuid, preview_link, _["mris"]),
                "dicomImages": self._handle_manifest(
                    uuid, preview_link, _["dicomImages"]
                ),
                "detailsReady": True,
            }
            result.append(dataset_format)
        return result
