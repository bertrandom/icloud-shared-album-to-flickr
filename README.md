# icloud-shared-album-to-flickr

This tool syncs photos from an iCloud Shared Album to Flickr.

Apple does not provide an API for iCloud Shared Albums (that I know of).

I reverse engineered the iCloud Shared Album Public Website FE code and used it simulate browser requests to the private iCloud APIs and extract the URLs of the photos. For each photo, I determine the largest file stored (by filesize) and upload this to Flickr as a photo visible to friends and family. It also adds the photo to a specified album.

It maintains a local cache of the photos that it has successfully uploaded to Flickr and will skip those photos on subsequent runs.

It is expected that this will be called from a cronjob.

## Usage

```
npm install
```

Copy `config/default.json5` to `config/local.json5` and fill in your Flickr credentials, iCloud Album Token, and Flickr album ID.

You can use [flickr-oauth-dance](https://www.npmjs.com/package/flickr-oauth-dance) to quickly generate Flickr credentials.

If your Public Website URL is

```
https://www.icloud.com/sharedalbum/#B0z5qAGN1JIFd3y
                                    ^^^^^^^^^^^^^^^
```

Then your iCloud Album Token is `B0z5qAGN1JIFd3y`.

Then simply run:

```
node app
```

If you need to clear the local cache:

```
rm -rf ./photosdb
```