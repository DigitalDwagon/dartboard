# dartboard
generalized Internet Archive upload target

dartboard is [available on PyPi](https://pypi.org/project/dartboard-ia) as `dartboard-ia`!

# Configuration
There are two ways you can provide dartboard with Internet Archive credentials to be able to upload:
- If you use the IA CLI (and you've run `ia configure`) dartboard will automatically use those credentials
- Otherwise, you can create a config json file (see below) and pass the location of it with `--config-path` (default: `./config.json`)

```json
{
  "s3_key": "abcdefg",
  "s3_secret": "abcdefg"
}
```

# Usage instructions

## Uploading a single item

You can upload a directory with
```bash
dartboard path/to/directory
```
dartboard will try to upload to the item with the same identifier as the name of the directory. If the item does not already exist on IA, you must specify the metadata by placing an `__ia_meta.json` file in the directory:
```json
{
  "collection": "opensource",
  "mediatype": "data",
  "title": "My title",
  "description": "My description",
  "foo": "bar"
}
```
You are required to specify, at minimum, a `collection` and `mediatype` to create a new item.

If the item already exists, dartboard will try to upload the files to it even if `__ia_meta.json` isn't present. If it is present, dartboard WILL attempt to diff the metadata and make the necessary changes once it has finished uploading the new files.

You can also specify dartboard settings for that item with an `__uploader_meta.json` file:
```java
{
  "setUploadState": false, // if enabled, dartboard will set an upload-state:uploading key on the item, and change it to upload-state:uploaded when done
  "setScanner": true, // if disabled, dartboard will not add "dartboard (vX.Y.Z)" to the scanner field
  "sendSizeHint": false, // if enabled, dartboard will send IA a size hint for the item, based on the size of files in the directory. Only enable this for new items, and only if you know that every file you want to upload to this item is already in the directory
  "derive": true // if disabled, dartboard will not queue a derive task once it has finished uploading
}
```
(Values shown above are the defaults. The comments in JSON are only to make reading easier - don't include them when running the actual command)

## Uploading multiple items (daemon mode)
dartboard can also be used to upload multiple items to the Internet Archive at once in daemon mode. 
```
dartboard --daemon
```
In daemon mode, dartboard will automatically upload any files inside the "staging directory" to corresponding items on the Internet Archive, and it will also watch that directory for new files and upload them as they arrive.

For example, let's say you make these files:
```
./dartboard-staging/IDENTIFIER1/foo.txt
./dartboard-staging/IDENTIFIER2/bar.txt
./dartboard-staging/IDENTIFIER2/__ia_meta.json
```
dartboard will automatically upload `foo.txt` to the item `IDENTIFIER1` if it already exists (if it does not exist, it will do nothing until you provide an `__ia_meta.json`). It will also upload `bar.txt` to `IDENTIFIER2`, creating the item if it does not exist since metadata is specified. Items are uploaded using the same rules as uploading a single directory.

It will automatically retry uploads until they are either successfully uploaded or attempted too many times, as specified in `config.json`. Items that fail too many times OR have errors that cannot be fixed by retrying (such as: the identifier is in use by an item your account cannot edit, the identifier is for an item that is darked, etc) will be moved to the failed item directory for manual intervention. Successfully uploaded files will either be moved to the done directory or automatically deleted, as specified in the config.