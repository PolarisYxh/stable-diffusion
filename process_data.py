from PIL import Image
import glob
import os
size = (512, 512)

# get all the files in a folder, make sure all are image files
files = glob.glob('./training_data/poem_image/images_text_pairs/*.PNG')

for fil in files:
    # implement file type checking here if required
    
    # get the basename, e.g. "dragon.jpg" -> ("dragon", ".jpg")
    basename = os.path.splitext(os.path.basename(fil))[0]

    with Image.open(fil) as img:
        # resize the image to 512 x 512
        img = img.resize(size)
        
        # rotate the image if required
        # img = img.rotate(90)
        
        # save the resized image, modify the resample method if required, modify the output directory as well
        img.save(f"./training_data/key/{basename}.png", format="PNG", resample=Image.Resampling.NEAREST)
