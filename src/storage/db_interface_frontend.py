import json
from copy import deepcopy
from typing import Union, Iterable, List, Tuple

from pymongo.cursor import Cursor

from helperFunctions.compare_sets import remove_duplicates_from_list
from helperFunctions.data_conversion import get_value_of_first_key
from helperFunctions.database_structure import visualize_complete_tree
from helperFunctions.file_tree import get_partial_virtual_path, FileTreeNode
from helperFunctions.tag import TagColor
from objects.file import FileObject
from objects.firmware import Firmware
from storage.db_interface_common import MongoInterfaceCommon


class FrontEndDbInterface(MongoInterfaceCommon):

    READ_ONLY = True

    def get_meta_list_from_id_list(self, id_iterable: Iterable[str], only_firmwares=False):
        if only_firmwares:
            fo_data = [self.get_joined_firmware_data(id_) for id_ in id_iterable]
        else:
            fo_data = [self.get_joined_firmware_data(id_) or self.file_objects.find_one(id_) for id_ in id_iterable]
        return self.get_meta_list(fo_data)

    def get_meta_list(self, fo_data: Union[List[dict], Cursor]) -> List[Tuple[str, str, dict, float]]:
        list_of_firmware_data = []
        for fo_entry in fo_data:
            if fo_entry:
                tags = fo_entry.get('tags', {})
                unpacker = self._get_unpacker_from_db_entry(fo_entry)
                tags[unpacker] = TagColor.LIGHT_BLUE
                submission_date = fo_entry.get('submission_date', 0)
                id_ = fo_entry['_id']
                if self.is_firmware_id(id_):
                    hid = self._create_firmware_hid_from_entry(fo_entry)
                else:
                    hid = self._get_hid_fo(id_)
                list_of_firmware_data.append((id_, hid, tags, submission_date))
        return list_of_firmware_data

    def _get_unpacker_from_db_entry(self, entry):
        if entry['processed_analysis']['unpacker']['file_system_flag']:
            unpacker = self.retrieve_analysis(deepcopy(entry['processed_analysis']))['unpacker']['plugin_used']
        else:
            unpacker = entry['processed_analysis']['unpacker']['plugin_used']
        return unpacker

    def get_hid(self, uid, root_uid=None):
        '''
        returns a human readable identifier (hid) for a given uid
        returns an empty string if uid is not in Database
        '''
        if self.is_firmware_id(uid):
            hid = self._get_hid_firmware(uid)
        else:
            hid = self._get_hid_fo(uid, root_uid)
        if not hid:
            hid = ''
        return hid

    def _get_hid_firmware(self, id_):
        firmware = self.firmware_metadata.find_one({'_id': id_}, {'vendor': 1, 'device_name': 1, 'device_part': 1, 'version': 1, 'device_class': 1})
        if firmware:
            return self._create_firmware_hid_from_entry(firmware)
        return None

    @staticmethod
    def _create_firmware_hid_from_entry(firmware):
        part = '' if 'device_part' not in firmware or firmware['device_part'] == '' else ' {}'.format(firmware['device_part'])
        return '{} {} -{} {} ({})'.format(
            firmware['vendor'], firmware['device_name'], part, firmware['version'], firmware['device_class'])

    def _get_hid_fo(self, uid, root_uid=None):
        file_object = self.file_objects.find_one({'_id': uid}, {'virtual_file_path': 1})
        if file_object is not None:
            return self._get_one_virtual_path_of_fo(file_object, root_uid)
        return None

    def get_data_for_nice_list(self, uid_list, root_uid):
        query = self._build_search_query_for_uid_list(uid_list)
        result = self.generate_nice_list_data(self.file_objects.find(query), root_uid)
        return result

    @staticmethod
    def generate_nice_list_data(db_iterable, root_uid):
        result = []
        for db_entry in db_iterable:
            if db_entry is not None:
                virtual_file_path = db_entry['virtual_file_path']
                result.append({
                    'uid': db_entry['_id'],
                    'files_included': db_entry['files_included'],
                    'size': db_entry['size'],
                    'mime-type': db_entry['processed_analysis']['file_type']['mime'] if 'file_type' in db_entry['processed_analysis'] else 'file-type-plugin/not-run-yet',
                    'virtual_file_paths': virtual_file_path[root_uid] if root_uid in virtual_file_path else get_value_of_first_key(virtual_file_path)
                })
        return result

    def get_file_name(self, uid):
        entry = self.file_objects.find_one({'_id': uid}, {'file_name': 1})
        return entry['file_name']

    def get_firmware_attribute_list(self, attribute, restrictions: dict = None):
        attribute_list = {
            entry[attribute]
            for entry in self.firmware_metadata.find(restrictions or {}, {attribute: 1})
        }
        return list(attribute_list)

    def get_device_class_list(self):
        return self.get_firmware_attribute_list('device_class')

    def get_vendor_list(self):
        return self.get_firmware_attribute_list('vendor')

    def get_device_name_dict(self):
        device_name_dict = {}
        for entry in self.firmware_metadata.find({}, {'device_class': 1, 'device_name': 1, 'vendor': 1}):
            device_class, device_name, vendor = entry['device_class'], entry['device_name'], entry['vendor']
            device_name_dict.setdefault(device_class, {})
            device_name_dict[device_class].setdefault(vendor, [])
            if device_name not in device_name_dict[device_class][vendor]:
                device_name_dict[device_class][vendor].append(device_name)
        return device_name_dict

    @staticmethod
    def _get_one_virtual_path_of_fo(fo_dict, root_uid):
        if root_uid is None or root_uid not in fo_dict['virtual_file_path'].keys():
            root_uid = list(fo_dict['virtual_file_path'].keys())[0]
        return FileObject.get_top_of_virtual_path(fo_dict['virtual_file_path'][root_uid][0])

    def all_uids_found_in_database(self, uid_list):
        if not uid_list:
            return True
        query = self._build_search_query_for_uid_list(uid_list)
        number_of_results = self.get_file_object_number(query)
        return len(uid_list) == number_of_results

    def generic_search(self, query: dict, skip=0, limit=0, only_fo_parent_firmware=False) -> List[str]:
        result = self._generic_search_firmware(query, skip, limit)
        if len(result) < limit or limit == 0:
            max_firmware_results = self.get_firmware_number(query)
            skip = skip - max_firmware_results if skip > max_firmware_results else 0
            limit = limit - len(result) if limit > 0 else 0
            if not only_fo_parent_firmware:
                result.extend(self._generic_search_files(query, skip, limit))
            else:
                result.extend(self._generic_search_parent_firmware(query, skip, limit))
        return remove_duplicates_from_list(result)

    def _generic_search_firmware(self, query, skip, limit):
        search_result = self.firmware_metadata.find(query, {'_id': 1}, sort=[('vendor', 1)], skip=skip, limit=limit)
        return [match['_id'] for match in search_result]

    def _generic_search_files(self, query, skip, limit):
        search_result = self.file_objects.find(query, {'_id': 1}, skip=skip, limit=limit, sort=[('file_name', 1)])
        return [match['_id'] for match in search_result]

    def _generic_search_parent_firmware(self, query, skip, limit):
        search_result = self.file_objects.find(query, {'virtual_file_path': 1})
        parent_uids = {uid for match in search_result for uid in match['virtual_file_path'].keys()}
        query_filter = {'uid': {'$in': list(parent_uids)}}
        return self._generic_search_firmware(query_filter, skip, limit)

    def get_other_versions_of_firmware(self, firmware: Firmware):
        query = {'vendor': firmware.vendor, 'device_name': firmware.device_name, 'device_part': firmware.device_part}
        search_result = self.firmware_metadata.find(query, {'_id': 1, 'version': 1})
        result = [r for r in search_result if r['_id'] != firmware.firmware_id]
        return result

    def get_last_added_firmwares(self, limit=10):
        latest_firmwares = self.perform_reverse_joined_firmware_query(
            {'submission_date': {'$gt': 1}}, sort={'submission_date': -1}, limit=limit
        )
        return self.get_meta_list(latest_firmwares)

    def get_latest_comments(self, limit=10):
        comments = []
        for collection in [self.file_objects]:
            db_entries = collection.aggregate([
                {'$match': {'comments': {'$not': {'$size': 0}}}},
                {'$project': {'_id': 1, 'comments': 1}},
                {'$unwind': {'path': '$comments'}},
                {'$sort': {'comments.time': -1}},
                {'$limit': limit}
            ])
            comments.extend([
                {**entry['comments'], 'uid': entry['_id']}  # caution: >=python3.5 exclusive syntax
                for entry in db_entries if entry['comments']
            ])
        comments.sort(key=lambda x: x['time'], reverse=True)
        return comments

    # --- file tree

    def _create_node_from_virtual_path(self, uid, root_uid, current_virtual_path, fo_data, whitelist=None):
        if len(current_virtual_path) > 1:  # in the middle of a virtual file path
            node = FileTreeNode(uid=None, root_uid=root_uid, virtual=True, name=current_virtual_path.pop(0))
            for child_node in self.generate_file_tree_node(uid, root_uid, current_virtual_path=current_virtual_path,
                                                           fo_data=fo_data, whitelist=whitelist):
                node.add_child_node(child_node)
        else:  # at the end of a virtual path aka a 'real' file
            if whitelist:
                has_children = any(f in fo_data['files_included'] for f in whitelist)
            else:
                has_children = fo_data['files_included'] != []
            mime_type = (fo_data['processed_analysis']['file_type']['mime']
                         if 'file_type' in fo_data['processed_analysis']
                         else 'file-type-plugin/not-run-yet')
            node = FileTreeNode(uid, root_uid=root_uid, virtual=False, name=fo_data['file_name'], size=fo_data['size'],
                                mime_type=mime_type, has_children=has_children)
        return node

    def generate_file_tree_node(self, uid, root_uid, current_virtual_path=None, fo_data=None, whitelist=None):
        required_fields = {'virtual_file_path': 1, 'files_included': 1, 'file_name': 1, 'size': 1,
                           'processed_analysis.file_type.mime': 1, '_id': 1}
        if fo_data is None:
            fo_data = self.get_specific_fields_of_db_entry({'_id': uid}, required_fields)
        try:
            if root_uid not in fo_data['virtual_file_path']:  # file tree for a file object (instead of a firmware)
                fo_data['virtual_file_path'] = get_partial_virtual_path(fo_data['virtual_file_path'], root_uid)
            if current_virtual_path is None:
                for entry in fo_data['virtual_file_path'][root_uid]:  # the same file may occur several times with different virtual paths
                    current_virtual_path = entry.split('/')[1:]
                    yield self._create_node_from_virtual_path(uid, root_uid, current_virtual_path, fo_data, whitelist)
            else:
                yield self._create_node_from_virtual_path(uid, root_uid, current_virtual_path, fo_data, whitelist)
        except Exception:  # the requested data is not present in the DB aka the file has not been analyzed yet
            yield FileTreeNode(uid=uid, root_uid=root_uid, not_analyzed=True, name='{} (not analyzed yet)'.format(uid))

    def get_number_of_total_matches(self, query: Union[dict, str], only_parent_firmwares: bool) -> int:
        if not only_parent_firmwares:
            return self.get_file_object_number(query=query)
        if isinstance(query, str):
            query = json.loads(query)
        fw_matches = {match['uid'] for match in self.perform_joined_firmware_query(query)}
        fo_matches = {
            parent
            for match in self.file_objects.find(query)
            for parent in match['virtual_file_path'].keys()
        } if query != {} else set()
        return len(fw_matches.union(fo_matches))

    def create_analysis_structure(self):
        if self.client.varietyResults.file_objectsKeys.count_documents({}) == 0:
            return 'Database statistics do not seem to be created yet.'

        file_object_keys = self.client.varietyResults.file_objectsKeys.find()
        all_field_strings = list(
            key_item['_id']['key'] for key_item in file_object_keys
            if key_item['_id']['key'].startswith('processed_analysis')
            and key_item['percentContaining'] >= float(self.config['data_storage']['structural_threshold'])
        )
        stripped_field_strings = list(field[len('processed_analysis.'):] for field in all_field_strings if field != 'processed_analysis')

        return visualize_complete_tree(stripped_field_strings)

    def rest_get_firmware_uids(self, offset, limit, query=None, recursive=False):
        if recursive:
            return self.generic_search(query=query, skip=offset, limit=limit, only_fo_parent_firmware=True)
        uid_cursor = self.perform_reverse_joined_firmware_query(query, skip=offset, limit=limit)
        return [result['_id'] for result in uid_cursor]

    def rest_get_file_object_uids(self, offset, limit, query=None):
        return self.rest_get_object_uids(offset, limit, query if query else dict())

    def rest_get_object_uids(self, offset, limit, query):
        uid_cursor = self.file_objects.find(query, {'_id': 1}).skip(offset).limit(limit)
        return [result['_id'] for result in uid_cursor]
