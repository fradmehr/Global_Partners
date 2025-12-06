Global Partners Project:

First, we use the AMAZON RDS to load the data files there; then using AMAZON GLUE, we schedule the process to read the files from RDS to S3.
Step by step process:
1. Use instruction video to write files into Amazon RDS
2. Create pyspark scripts for GLUE (1.Amazon_Glue_Script.txt); and create the job details with IAM role with below credentials; and create a daily schedule for it:
IAM credentials:
Policies: AmazonS3FullAccess; AWSGlueServiceRole
3. Create S3 bucket as the destination to load the files
4. Create IAM user to read files in S3 from local python code. Use (2. S3 to local computer-Global Partners.ipynb) to read files.
5. Run python code to create streamlit app (glabalpartners_questions.py) in terminal.  
cd <directory>
streamlit run glabalpartners_questions.py

<img width="2644" height="724" alt="image" src="https://github.com/user-attachments/assets/8902724f-25fe-4a77-935f-cbeb79d39d3b" />
