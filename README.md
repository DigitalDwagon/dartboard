# dartboard
generalized Internet Archive upload target

## Configuration
To use dartboard, you'll need a `config.json` in the same directory where you run it (or pass the location of it with `--config-path` in your dartboard command).
```json
{
  "s3_key": "abcdefg",
  "s3_secret": "abcdefg"
}
```

## Usage instructions
You can upload a directory with
```bash
dartboard path/to/directory
```
dartboard will try to upload to the item with the same identifier as the name of the directory. If the item does not exist, you must specify the metadata by placing an `__ia_meta.json` file in the directory:
```json
{
  "collection": "opensource",
  "mediatype": "data",
  "title": "My title",
  "description": "My description",
  "foo": "bar"
}
```
If the item already exists, dartboard will try to upload the files to it with or without `__ia_meta.json` being present. If it is present, dartboard WILL attempt to diff the metadata and make the necessary changes once it has finished uploading the new files.

You can also specify dartboard settings for that item with an `__uploader_meta.json` file:
```json
{
  "setUploadState": false, // if enabled, dartboard will set an upload-state:uploading key on the item, and change it to upload-state:uploaded when done
  "setScanner": true, // if disabled, dartboard will not add "dartboard (vX.Y.Z)" to the scanner field
  "sendSizeHint": false, // if enabled, dartboard will send IA a size hint for the item, based on the size of files in the directory
  "derive": true // if disabled, dartboard will not queue a derive task once it has finished uploading
}
```
